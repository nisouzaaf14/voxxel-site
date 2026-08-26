import os
import io
import json
import time
import secrets
from datetime import timedelta
from urllib.parse import urlparse
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, g, Response, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image

from database import init_db, get_db, CATEGORIAS, to_blob, criar_pedido, get_configs, set_configs
from calculadora import calcular_orcamento, formatar_horas, MATERIAIS, QUALIDADE, COMPLEXIDADE
import pix
import mercadopago_pay

SECRET_KEY_PADRAO = "troque-esta-chave-em-producao"
ADMIN_PASSWORD_PADRAO = "voxxel123"

ADMIN_PASSWORD = os.environ.get("VOXXEL_ADMIN_PASSWORD", ADMIN_PASSWORD_PADRAO)
SECRET_KEY = os.environ.get("VOXXEL_SECRET_KEY", SECRET_KEY_PADRAO)

TIPOS_IMAGEM_PERMITIDOS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

app = Flask(__name__)
app.secret_key = SECRET_KEY

# O Render/Heroku (e qualquer PaaS) coloca a aplicação atrás de um proxy
# reverso: sem isso, o Flask acha que toda requisição chegou por HTTP puro
# (mesmo quando o visitante acessou via HTTPS), o que quebra o cookie
# "Secure" e os links de retorno do Mercado Pago.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # limite de 5MB por upload
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Em produção (Render, com HTTPS) isso deve ficar "true" -- o Render já
    # define a variável PORT automaticamente, então usamos isso como pista
    # pra saber se estamos rodando publicado ou só testando localmente.
    SESSION_COOKIE_SECURE=os.environ.get(
        "VOXXEL_COOKIE_SECURE", "true" if os.environ.get("PORT") else "false"
    ).lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=4),
)

if SECRET_KEY == SECRET_KEY_PADRAO:
    print(
        "[AVISO DE SEGURANÇA] VOXXEL_SECRET_KEY não foi definida -- usando uma chave "
        "padrão insegura. Defina essa variável de ambiente antes de publicar o site "
        "(qualquer valor aleatório e longo serve)."
    )
if ADMIN_PASSWORD == ADMIN_PASSWORD_PADRAO:
    print(
        "[AVISO DE SEGURANÇA] VOXXEL_ADMIN_PASSWORD não foi definida -- o painel está "
        "usando a senha padrão de exemplo. Troque isso antes de publicar o site."
    )

with app.app_context():
    init_db()


def login_obrigatorio(rota):
    """Decorator que protege qualquer rota do admin -- centraliza a checagem
    de sessão pra não depender de lembrar de repetir o `if` em cada função."""
    @wraps(rota)
    def rota_protegida(*args, **kwargs):
        if not session.get("admin_logado"):
            return redirect(url_for("admin_login"))
        return rota(*args, **kwargs)
    return rota_protegida


# ---------- proteção CSRF ----------
# O Flask não valida token CSRF por padrão. Como o site tem vários POSTs
# (carrinho, checkout, formulários do admin), geramos um token por sessão e
# exigimos que todo POST venha com ele -- assim uma página maliciosa em
# outro site não consegue disparar ações aqui usando a sessão do usuário.

def gerar_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = gerar_csrf_token

# O webhook do Mercado Pago é chamado pelo servidor deles, não por um
# navegador com nossa sessão -- não tem como (nem faz sentido) ele mandar
# nosso token CSRF, então essa rota fica de fora dessa checagem.
ENDPOINTS_SEM_CSRF = {"webhook_mercadopago"}


@app.before_request
def protecao_csrf():
    if request.method == "POST" and request.endpoint not in ENDPOINTS_SEM_CSRF:
        token_sessao = session.get("csrf_token", "")
        token_enviado = request.form.get("csrf_token", "")
        if not token_sessao or not secrets.compare_digest(token_sessao, token_enviado):
            abort(400)


@app.errorhandler(400)
def erro_validacao(e):
    flash("Sua sessão expirou ou a página estava desatualizada. Tente novamente.")
    return redirect(redirecionamento_seguro(url_for("home"))), 303


@app.after_request
def adicionar_headers_seguranca(resposta):
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resposta.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # unsafe-inline é necessário porque o site usa scripts/estilos inline
    # nas páginas (calculadora, chat, chips) -- ainda assim isso bloqueia
    # scripts vindos de fora, iframes de terceiros e plugins tipo Flash/Java.
    resposta.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' https:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )
    if request.is_secure:
        resposta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resposta


@app.errorhandler(413)
def imagem_grande_demais(e):
    flash("A imagem enviada é grande demais. O limite é 5MB.")
    return redirect(redirecionamento_seguro(url_for("admin_produtos")))


# ---------- rate limiting simples do login do admin ----------
# Guarda em memória (reseta se o processo reiniciar) -- suficiente pra um
# site pequeno rodando numa única instância; freia ataques de força bruta
# na senha do painel sem precisar de infraestrutura extra (Redis etc).
_LOGIN_TENTATIVAS = {}
LOGIN_MAX_TENTATIVAS = 6
LOGIN_JANELA_SEGUNDOS = 5 * 60


def login_bloqueado(ip):
    agora = time.time()
    tentativas = [t for t in _LOGIN_TENTATIVAS.get(ip, []) if agora - t < LOGIN_JANELA_SEGUNDOS]
    _LOGIN_TENTATIVAS[ip] = tentativas
    return len(tentativas) >= LOGIN_MAX_TENTATIVAS


def registrar_falha_login(ip):
    _LOGIN_TENTATIVAS.setdefault(ip, []).append(time.time())


# ---------- helpers ----------

def redirecionamento_seguro(padrao):
    """Redireciona de volta pra página anterior (via Referer) só quando ela
    é do próprio site -- evita que alguém monte um link/formulário externo
    que faça o usuário ser redirecionado pra um site malicioso depois de
    uma ação aqui (open redirect)."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return ref
    return padrao


def texto_seguro(valor, tamanho_max):
    return (valor or "").strip()[:tamanho_max]


def carrinho_sessao():
    return session.setdefault("carrinho", {})  # { "produto_id": quantidade }


def carrinho_detalhado(conn):
    itens = []
    total = 0.0
    for pid, qtd in carrinho_sessao().items():
        row = conn.execute(
            f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE id = ?", (int(pid),)
        ).fetchone()
        if row:
            subtotal = row["preco"] * qtd
            total += subtotal
            itens.append({"produto": row, "qtd": qtd, "subtotal": subtotal})
    return itens, total


@app.context_processor
def inject_globals():
    qtd_carrinho = sum(carrinho_sessao().values())
    chat_auto_message = session.pop("voxxel_chat_auto", None)
    return dict(categorias=CATEGORIAS, qtd_carrinho=qtd_carrinho, chat_auto_message=chat_auto_message)


# ---------- páginas públicas ----------

@app.route("/")
def home():
    return render_template("index.html")


# Colunas usadas nas listagens (loja e admin): evita carregar o BLOB da
# imagem inteiro só pra mostrar a lista de produtos -- só um indicador
# booleano (tem_imagem) que a rota /produto/<id>/imagem resolve de verdade.
COLUNAS_PRODUTO_LISTA = """
    id, nome, categoria, preco, descricao, imagem_ang, ativo, estoque,
    (imagem_mimetype IS NOT NULL) AS tem_imagem
"""


@app.route("/loja")
def loja():
    conn = get_db()
    categoria = request.args.get("categoria", "todos")
    if categoria == "todos":
        produtos = conn.execute(
            f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE ativo = 1 ORDER BY id DESC"
        ).fetchall()
    else:
        produtos = conn.execute(
            f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE ativo = 1 AND categoria = ? ORDER BY id DESC",
            (categoria,),
        ).fetchall()
    conn.close()
    return render_template("loja.html", produtos=produtos, categoria_ativa=categoria)


@app.route("/produto/<int:produto_id>")
def produto_detalhe(produto_id):
    conn = get_db()
    produto = conn.execute(
        f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE id = ? AND ativo = 1", (produto_id,)
    ).fetchone()
    relacionados = []
    if produto:
        relacionados = conn.execute(
            f"""SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos
                WHERE ativo = 1 AND categoria = ? AND id != ? ORDER BY id DESC LIMIT 3""",
            (produto["categoria"], produto_id),
        ).fetchall()
    conn.close()
    if not produto:
        flash("Esse produto não está mais disponível.")
        return redirect(url_for("loja"))
    return render_template("produto_detalhe.html", p=produto, relacionados=relacionados)


@app.route("/carrinho/adicionar/<int:produto_id>", methods=["POST"])
def carrinho_adicionar(produto_id):
    conn = get_db()
    produto = conn.execute("SELECT estoque FROM produtos WHERE id = ? AND ativo = 1", (produto_id,)).fetchone()
    conn.close()
    if not produto:
        flash("Esse produto não está mais disponível.")
        return redirect(redirecionamento_seguro(url_for("loja")))

    try:
        quantidade_pedida = max(1, int(request.form.get("quantidade", 1) or 1))
    except ValueError:
        quantidade_pedida = 1
    carrinho = carrinho_sessao()
    pid = str(produto_id)
    nova_qtd = carrinho.get(pid, 0) + quantidade_pedida

    if produto["estoque"] is not None:
        if produto["estoque"] <= 0:
            flash("Esse produto está esgotado no momento.")
            return redirect(redirecionamento_seguro(url_for("loja")))
        nova_qtd = min(nova_qtd, produto["estoque"])

    carrinho[pid] = nova_qtd
    session["carrinho"] = carrinho
    session.modified = True
    flash("Produto adicionado ao carrinho.")
    return redirect(redirecionamento_seguro(url_for("loja")))


@app.route("/carrinho/remover/<int:produto_id>", methods=["POST"])
def carrinho_remover(produto_id):
    carrinho = carrinho_sessao()
    carrinho.pop(str(produto_id), None)
    session["carrinho"] = carrinho
    session.modified = True
    return redirect(url_for("carrinho"))


@app.route("/carrinho")
def carrinho():
    conn = get_db()
    itens, total = carrinho_detalhado(conn)
    conn.close()
    return render_template("carrinho.html", itens=itens, total=total)


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    conn = get_db()
    itens, total = carrinho_detalhado(conn)
    if not itens:
        conn.close()
        return redirect(url_for("loja"))

    if request.method == "POST":
        nome = texto_seguro(request.form.get("nome"), 120)
        telefone = texto_seguro(request.form.get("telefone"), 40)
        forma_pagamento = request.form.get("forma_pagamento", "pix")
        if forma_pagamento not in ("pix", "cartao", "combinar"):
            forma_pagamento = "combinar"

        if not nome or not telefone:
            flash("Preencha nome e telefone para finalizar o pedido.")
            conn.close()
            return render_template("checkout.html", itens=itens, total=total)

        linhas = [f"{i['qtd']}x {i['produto']['nome']} - R$ {i['subtotal']:.2f}".replace(".", ",") for i in itens]
        detalhes = "\n".join(linhas)

        pedido_id = criar_pedido(conn, "loja", detalhes, total, nome, telefone, forma_pagamento)
        conn.close()

        session["carrinho"] = {}
        session.modified = True

        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    conn.close()
    return render_template("checkout.html", itens=itens, total=total)


@app.route("/pedido/<int:pedido_id>/pagamento")
def pedido_pagamento(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)
    conn.close()
    if not pedido:
        return redirect(url_for("home"))

    pix_disponivel = bool(config["pix_chave"].strip()) and pedido["forma_pagamento"] == "pix"
    cartao_disponivel = bool(config["mp_access_token"].strip()) and pedido["forma_pagamento"] == "cartao"
    pix_payload = ""
    if pix_disponivel:
        pix_payload = pix.gerar_payload(
            config["pix_chave"], config["pix_nome"], config["pix_cidade"],
            pedido["valor_estimado"], txid=f"VOXXEL{pedido_id}",
        )
    return render_template(
        "pagamento.html", pedido=pedido, pix_disponivel=pix_disponivel,
        cartao_disponivel=cartao_disponivel, pix_payload=pix_payload, config=config,
    )


@app.route("/pedido/<int:pedido_id>/pagar-cartao")
def pedido_pagar_cartao(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)
    if not pedido or not config["mp_access_token"].strip():
        conn.close()
        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    url_base = request.url_root.rstrip("/")
    descricao = f"Pedido Voxxel #{pedido_id} - {pedido['detalhes'].splitlines()[0]}"
    try:
        preference_id, init_point = mercadopago_pay.criar_preferencia(
            config["mp_access_token"], pedido_id, descricao, pedido["valor_estimado"], url_base
        )
    except Exception:
        conn.close()
        flash("Não foi possível abrir o pagamento por cartão agora. Tente novamente ou fale com a gente.")
        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    if not init_point:
        conn.close()
        flash("Não foi possível abrir o pagamento por cartão agora. Tente novamente ou fale com a gente.")
        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    conn.execute("UPDATE pedidos SET mp_preference_id = ? WHERE id = ?", (preference_id, pedido_id))
    conn.commit()
    conn.close()
    return redirect(init_point)


@app.route("/pedido/<int:pedido_id>/retorno-cartao")
def pedido_retorno_cartao(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)

    if pedido and config["mp_access_token"].strip():
        payment_id = request.args.get("payment_id") or request.args.get("collection_id")
        status = None
        if payment_id:
            try:
                dados_pagamento = mercadopago_pay.consultar_pagamento(config["mp_access_token"], payment_id)
                status = dados_pagamento.get("status")
            except Exception:
                status = None
        if status == "approved":
            conn.execute(
                "UPDATE pedidos SET status_pagamento = 'informado', mp_payment_id = ? WHERE id = ?",
                (payment_id, pedido_id),
            )
            conn.commit()
            msg = (
                f"Olá! Acabei de pagar com cartão o pedido #{pedido_id} no site da Voxxel 🙂\n\n"
                f"{pedido['detalhes']}\n\nTotal: R$ {pedido['valor_estimado']:.2f}".replace(".", ",")
            )
            session["voxxel_chat_auto"] = msg
            session.modified = True
            flash("Pagamento aprovado! Confirme com nosso assistente virtual.")
        elif status == "pending" or request.args.get("status") == "pending":
            flash("Pagamento em análise. Assim que for aprovado, atualizamos seu pedido.")
        else:
            flash("O pagamento não foi concluído. Você pode tentar novamente ou combinar direto com a gente.")
    conn.close()
    return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))


@app.route("/webhooks/mercadopago", methods=["POST", "GET"])
def webhook_mercadopago():
    """Notificação automática do Mercado Pago (IPN). É a forma confiável de
    saber que um pagamento por cartão foi aprovado, mesmo se o cliente
    fechar a aba antes de voltar pro site."""
    payment_id = request.args.get("data.id") or (request.get_json(silent=True) or {}).get("data", {}).get("id")
    if not payment_id:
        return "", 200

    conn = get_db()
    config = get_configs(conn)
    if not config["mp_access_token"].strip():
        conn.close()
        return "", 200

    try:
        dados_pagamento = mercadopago_pay.consultar_pagamento(config["mp_access_token"], payment_id)
    except Exception:
        conn.close()
        return "", 200

    pedido_id = dados_pagamento.get("external_reference")
    if pedido_id and dados_pagamento.get("status") == "approved":
        try:
            pedido_id = int(pedido_id)
        except (TypeError, ValueError):
            conn.close()
            return "", 200
        conn.execute(
            "UPDATE pedidos SET status_pagamento = 'informado', mp_payment_id = ? WHERE id = ?",
            (payment_id, pedido_id),
        )
        conn.commit()
    conn.close()
    return "", 200


@app.route("/pedido/<int:pedido_id>/pix.png")
def pedido_pix_png(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT valor_estimado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)
    conn.close()
    if not pedido or not config["pix_chave"].strip():
        return "", 404

    payload = pix.gerar_payload(
        config["pix_chave"], config["pix_nome"], config["pix_cidade"],
        pedido["valor_estimado"], txid=f"VOXXEL{pedido_id}",
    )
    png = pix.gerar_qrcode_png(payload)
    resposta = Response(png, mimetype="image/png")
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.route("/pedido/<int:pedido_id>/confirmar-pagamento", methods=["POST"])
def pedido_confirmar_pagamento(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if pedido:
        conn.execute("UPDATE pedidos SET status_pagamento = 'informado' WHERE id = ?", (pedido_id,))
        conn.commit()

        linhas = pedido["detalhes"]
        msg = (
            f"Olá! Acabei de fazer o pagamento do pedido #{pedido_id} no site da Voxxel 🙂\n\n"
            f"{linhas}\n\n"
            f"Total: R$ {pedido['valor_estimado']:.2f}".replace(".", ",")
        )
        session["voxxel_chat_auto"] = msg
        session.modified = True
        flash("Pagamento informado! Confirme com nosso assistente virtual.")
    conn.close()
    return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))


@app.route("/orcamento", methods=["GET", "POST"])
def orcamento():
    resultado = None
    form = {
        "categoria": "tecnica", "altura": 10, "largura": 10, "profundidade": 10,
        "quantidade": 1, "material": "pla", "qualidade": "padrao", "complexidade": "media",
    }

    if request.method == "POST":
        try:
            form.update({
                "categoria": request.form.get("categoria", "tecnica"),
                "altura": max(0.0, float(request.form.get("altura") or 0)),
                "largura": max(0.0, float(request.form.get("largura") or 0)),
                "profundidade": max(0.0, float(request.form.get("profundidade") or 0)),
                "quantidade": max(1, int(request.form.get("quantidade") or 1)),
                "material": request.form.get("material", "pla"),
                "qualidade": request.form.get("qualidade", "padrao"),
                "complexidade": request.form.get("complexidade", "media"),
            })
        except (ValueError, TypeError):
            flash("Verifique os valores preenchidos na calculadora.")
            return render_template(
                "orcamento.html", form=form, resultado=None,
                materiais=MATERIAIS, qualidades=QUALIDADE, complexidades=COMPLEXIDADE,
                materiais_js=json.dumps(MATERIAIS), qualidade_js=json.dumps(QUALIDADE),
                complexidade_js=json.dumps(COMPLEXIDADE),
            )
        resultado = calcular_orcamento(
            form["altura"], form["largura"], form["profundidade"], form["quantidade"],
            form["categoria"], form["complexidade"], form["material"], form["qualidade"],
        )
        resultado["tempo_formatado"] = formatar_horas(resultado["horas_total"])

        if request.form.get("acao") == "enviar":
            nome = texto_seguro(request.form.get("nome"), 120)
            telefone = texto_seguro(request.form.get("telefone"), 40)

            if not nome or not telefone:
                flash("Preencha nome e telefone para enviar o orçamento.")
                return render_template(
                    "orcamento.html", form=form, resultado=resultado,
                    materiais=MATERIAIS, qualidades=QUALIDADE, complexidades=COMPLEXIDADE,
                    materiais_js=json.dumps(MATERIAIS), qualidade_js=json.dumps(QUALIDADE),
                    complexidade_js=json.dumps(COMPLEXIDADE),
                )

            detalhes = (
                f"Categoria: {resultado['categoria_nome']}\n"
                f"Dimensões: {form['altura']}x{form['largura']}x{form['profundidade']} cm\n"
                f"Material: {resultado['material_nome']}\n"
                f"Qualidade: {form['qualidade']}\n"
                f"Quantidade: {form['quantidade']}"
            )
            conn = get_db()
            pedido_id = criar_pedido(conn, "orcamento", detalhes, resultado["preco_total"], nome, telefone, "pix")
            conn.close()

            flash("Orçamento recebido! Você pode adiantar o pagamento por Pix ou combinar direto com a gente.")
            return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    return render_template(
        "orcamento.html", form=form, resultado=resultado,
        materiais=MATERIAIS, qualidades=QUALIDADE, complexidades=COMPLEXIDADE,
        materiais_js=json.dumps(MATERIAIS), qualidade_js=json.dumps(QUALIDADE),
        complexidade_js=json.dumps(COMPLEXIDADE),
    )


# ---------- admin ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    erro = None
    ip = request.remote_addr or "desconhecido"
    if request.method == "POST":
        if login_bloqueado(ip):
            erro = "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."
        elif secrets.compare_digest(request.form.get("senha", ""), ADMIN_PASSWORD):
            session.clear()
            session["admin_logado"] = True
            session.permanent = True  # expira sozinho após PERMANENT_SESSION_LIFETIME
            return redirect(url_for("admin_dashboard"))
        else:
            registrar_falha_login(ip)
            erro = "Senha incorreta."
    return render_template("admin_login.html", erro=erro)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logado", None)
    return redirect(url_for("home"))


@app.route("/admin")
@login_obrigatorio
def admin_dashboard():
    conn = get_db()
    pedidos = conn.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    produtos = conn.execute("SELECT id, ativo, estoque FROM produtos").fetchall()
    config = get_configs(conn)
    conn.close()

    receita_confirmada = sum(p["valor_estimado"] for p in pedidos if p["status_pagamento"] == "informado")
    pedidos_novos = sum(1 for p in pedidos if p["status"] == "novo")
    total_pedidos = len(pedidos)
    ticket_medio = (sum(p["valor_estimado"] for p in pedidos) / total_pedidos) if total_pedidos else 0
    produtos_ativos = sum(1 for p in produtos if p["ativo"])
    produtos_esgotados = sum(1 for p in produtos if p["estoque"] is not None and p["estoque"] <= 0)

    return render_template(
        "admin_dashboard.html",
        receita_confirmada=receita_confirmada,
        pedidos_novos=pedidos_novos,
        total_pedidos=total_pedidos,
        ticket_medio=ticket_medio,
        produtos_ativos=produtos_ativos,
        produtos_esgotados=produtos_esgotados,
        pix_configurado=bool(config["pix_chave"].strip()),
        cartao_configurado=bool(config["mp_access_token"].strip()),
        ultimos_pedidos=pedidos[:5],
    )


@app.route("/admin/configuracoes", methods=["GET", "POST"])
@login_obrigatorio
def admin_configuracoes():
    conn = get_db()
    if request.method == "POST":
        set_configs(conn, {
            "pix_chave": request.form.get("pix_chave", "").strip(),
            "pix_nome": request.form.get("pix_nome", "").strip() or "Voxxel Impressao 3D",
            "pix_cidade": request.form.get("pix_cidade", "").strip() or "Sao Jose dos Pinhais",
            "whatsapp": request.form.get("whatsapp", "").strip(),
            "mp_access_token": request.form.get("mp_access_token", "").strip(),
        })
        flash("Configurações salvas.")
        conn.close()
        return redirect(url_for("admin_configuracoes"))
    config = get_configs(conn)
    conn.close()
    return render_template("admin_configuracoes.html", config=config)


@app.route("/admin/produtos")
@login_obrigatorio
def admin_produtos():
    conn = get_db()
    produtos = conn.execute(f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin_produtos.html", produtos=produtos)


def processar_upload_imagem(arquivo):
    """Lê o arquivo enviado no formulário e valida formato/conteúdo.
    Retorna (bytes, mimetype) ou None se não veio nenhum arquivo.

    Não confia só na extensão/mimetype que o navegador informou (fácil de
    forjar) -- abre o arquivo de verdade com o Pillow pra confirmar que os
    bytes são realmente uma imagem válida antes de guardar no banco."""
    if not arquivo or not arquivo.filename:
        return None
    if arquivo.mimetype not in TIPOS_IMAGEM_PERMITIDOS:
        flash("Formato de imagem não suportado. Envie um JPG, PNG ou WEBP.")
        return None
    dados = arquivo.read()
    if not dados:
        return None
    try:
        imagem = Image.open(io.BytesIO(dados))
        imagem.verify()
    except Exception:
        flash("O arquivo enviado não é uma imagem válida.")
        return None
    return dados, arquivo.mimetype


def ler_estoque_formulario():
    """Campo de estoque é opcional: vazio = estoque ilimitado (None)."""
    bruto = request.form.get("estoque", "").strip()
    if bruto == "":
        return None
    try:
        return max(0, int(bruto))
    except ValueError:
        return None


def ler_preco_formulario():
    try:
        return max(0.0, round(float(request.form.get("preco", "0").replace(",", ".")), 2))
    except (ValueError, TypeError):
        return None


@app.route("/admin/produtos/novo", methods=["GET", "POST"])
@login_obrigatorio
def admin_produto_novo():
    if request.method == "POST":
        preco = ler_preco_formulario()
        if preco is None or not request.form.get("nome", "").strip():
            flash("Preencha nome e preço corretamente.")
            return render_template("admin_produto_form.html", produto=None)

        conn = get_db()
        resultado_imagem = processar_upload_imagem(request.files.get("imagem"))
        imagem_dados, imagem_mimetype = resultado_imagem if resultado_imagem else (None, None)
        conn.execute(
            """INSERT INTO produtos
               (nome, categoria, preco, descricao, imagem_ang, ativo, imagem_dados, imagem_mimetype, estoque)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                texto_seguro(request.form["nome"], 120), request.form["categoria"], preco,
                texto_seguro(request.form.get("descricao"), 2000), request.form.get("imagem_ang", "0deg"),
                1 if request.form.get("ativo") else 0,
                to_blob(imagem_dados), imagem_mimetype, ler_estoque_formulario(),
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_produtos"))
    return render_template("admin_produto_form.html", produto=None)


@app.route("/admin/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@login_obrigatorio
def admin_produto_editar(produto_id):
    conn = get_db()
    if request.method == "POST":
        preco = ler_preco_formulario()
        if preco is None or not request.form.get("nome", "").strip():
            flash("Preencha nome e preço corretamente.")
            conn.close()
            return redirect(url_for("admin_produto_editar", produto_id=produto_id))

        resultado_imagem = processar_upload_imagem(request.files.get("imagem"))
        estoque = ler_estoque_formulario()
        nome = texto_seguro(request.form["nome"], 120)
        descricao = texto_seguro(request.form.get("descricao"), 2000)

        if resultado_imagem:
            # Nova imagem enviada: substitui a anterior
            imagem_dados, imagem_mimetype = resultado_imagem
            conn.execute(
                """UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?,
                   imagem_dados=?, imagem_mimetype=?, estoque=? WHERE id=?""",
                (
                    nome, request.form["categoria"], preco,
                    descricao, request.form.get("imagem_ang", "0deg"),
                    1 if request.form.get("ativo") else 0,
                    to_blob(imagem_dados), imagem_mimetype, estoque, produto_id,
                ),
            )
        elif request.form.get("remover_imagem"):
            # Usuário marcou pra remover a imagem atual, sem enviar outra
            conn.execute(
                """UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?,
                   imagem_dados=NULL, imagem_mimetype=NULL, estoque=? WHERE id=?""",
                (
                    nome, request.form["categoria"], preco,
                    descricao, request.form.get("imagem_ang", "0deg"),
                    1 if request.form.get("ativo") else 0, estoque, produto_id,
                ),
            )
        else:
            # Mantém a imagem que já existia
            conn.execute(
                "UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?, estoque=? WHERE id=?",
                (
                    nome, request.form["categoria"], preco,
                    descricao, request.form.get("imagem_ang", "0deg"),
                    1 if request.form.get("ativo") else 0, estoque, produto_id,
                ),
            )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_produtos"))
    produto = conn.execute(
        f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE id = ?", (produto_id,)
    ).fetchone()
    conn.close()
    return render_template("admin_produto_form.html", produto=produto)


@app.route("/admin/produtos/<int:produto_id>/toggle", methods=["POST"])
@login_obrigatorio
def admin_produto_toggle(produto_id):
    conn = get_db()
    row = conn.execute("SELECT ativo FROM produtos WHERE id = ?", (produto_id,)).fetchone()
    if row is not None:
        novo_status = 0 if row["ativo"] else 1
        conn.execute("UPDATE produtos SET ativo = ? WHERE id = ?", (novo_status, produto_id))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_produtos"))


@app.route("/produto/<int:produto_id>/imagem")
def produto_imagem(produto_id):
    conn = get_db()
    row = conn.execute(
        "SELECT imagem_dados, imagem_mimetype FROM produtos WHERE id = ?", (produto_id,)
    ).fetchone()
    conn.close()
    if not row or not row["imagem_mimetype"] or not row["imagem_dados"]:
        return "", 404
    resposta = Response(bytes(row["imagem_dados"]), mimetype=row["imagem_mimetype"])
    resposta.headers["Cache-Control"] = "public, max-age=86400"
    return resposta


@app.route("/admin/produtos/<int:produto_id>/excluir", methods=["POST"])
@login_obrigatorio
def admin_produto_excluir(produto_id):
    conn = get_db()
    conn.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_produtos"))


@app.route("/admin/pedidos")
@login_obrigatorio
def admin_pedidos():
    status_filtro = request.args.get("status", "todos")
    conn = get_db()
    todos = conn.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    conn.close()

    contagens = {"todos": len(todos), "novo": 0, "andamento": 0, "concluido": 0}
    for p in todos:
        contagens[p["status"]] = contagens.get(p["status"], 0) + 1

    if status_filtro in ("novo", "andamento", "concluido"):
        pedidos = [p for p in todos if p["status"] == status_filtro]
    else:
        status_filtro = "todos"
        pedidos = todos

    return render_template(
        "admin_pedidos.html", pedidos=pedidos, status_filtro=status_filtro, contagens=contagens
    )


@app.route("/admin/pedidos/<int:pedido_id>/status", methods=["POST"])
@login_obrigatorio
def admin_pedido_status(pedido_id):
    novo_status = request.form.get("status", "")
    if novo_status not in ("novo", "andamento", "concluido"):
        flash("Status inválido.")
        return redirect(url_for("admin_pedidos"))
    conn = get_db()
    conn.execute("UPDATE pedidos SET status = ? WHERE id = ?", (novo_status, pedido_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_pedidos"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Por padrão, debug fica DESLIGADO -- precisa ligar explicitamente em
    # desenvolvimento. Rodar com o debugger do Werkzeug ligado em produção é
    # uma falha grave de segurança: ele permite executar código arbitrário
    # no servidor pra quem encontrar uma página de erro.
    debug = os.environ.get("VOXXEL_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
