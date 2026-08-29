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
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image

from database import (
    init_db, get_db, CATEGORIAS, to_blob, criar_pedido, get_configs, set_configs, USING_POSTGRES,
    normalizar_telefone, criar_cliente, buscar_cliente_por_telefone, buscar_cliente_por_id,
    listar_pedidos_cliente, ler_coordenada_formulario,
    criar_impressora, buscar_impressora_por_telefone, buscar_impressora_por_id, listar_impressoras,
    definir_status_impressora, atualizar_localizacao_impressora, definir_impressora_ativa,
    listar_pedidos_da_impressora, resumo_comissoes, percentual_comissao,
)
from calculadora import (
    calcular_orcamento, formatar_horas, MATERIAIS, QUALIDADE, COMPLEXIDADE,
    PRECO_HORA_IMPRESSAO, SHELL_FRACTION, CAT_ACABAMENTO,
)
import pix
import mercadopago_pay
import distribuicao

SECRET_KEY_PADRAO = "troque-esta-chave-em-producao"
ADMIN_PASSWORD_PADRAO = "@NI04041"

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

print(
    "[BANCO DE DADOS] Usando " + ("PostgreSQL (dados permanentes)." if USING_POSTGRES
    else "SQLite local -- ATENÇÃO: em serviços como o Render, sem a variável "
         "DATABASE_URL configurada, esses dados são apagados a cada deploy/reinício.")
)


def login_obrigatorio(rota):
    """Decorator que protege qualquer rota do admin -- centraliza a checagem
    de sessão pra não depender de lembrar de repetir o `if` em cada função."""
    @wraps(rota)
    def rota_protegida(*args, **kwargs):
        if not session.get("admin_logado"):
            return redirect(url_for("admin_login"))
        return rota(*args, **kwargs)
    return rota_protegida


def login_cliente_obrigatorio(rota):
    """Mesma ideia do `login_obrigatorio`, mas para a conta do cliente --
    protege checkout e envio de orçamento, que agora exigem login."""
    @wraps(rota)
    def rota_protegida(*args, **kwargs):
        if not session.get("cliente_id"):
            flash("Faça login para continuar.")
            return redirect(url_for("conta_entrar", next=request.path))
        return rota(*args, **kwargs)
    return rota_protegida


def login_impressora_obrigatorio(rota):
    """Mesma ideia do `login_cliente_obrigatorio`, só que pra conta da
    impressora parceira -- protege o painel dela (status, ofertas, etc)."""
    @wraps(rota)
    def rota_protegida(*args, **kwargs):
        if not session.get("impressora_id"):
            flash("Faça login para acessar o painel da impressora.")
            return redirect(url_for("impressora_entrar", next=request.path))
        return rota(*args, **kwargs)
    return rota_protegida


def next_seguro(padrao):
    """Valida o parâmetro `next` (pra onde voltar depois do login) -- só
    aceita caminhos internos começando com uma única barra, pra ninguém usar
    isso pra redirecionar o cliente logado pra um site de fora (open redirect)."""
    destino = request.values.get("next", "")
    if destino.startswith("/") and not destino.startswith("//"):
        return destino
    return padrao


def pedido_pertence_ao_usuario(pedido):
    """Pedidos feitos antes de existir login de cliente ficam com cliente_id
    nulo e continuam acessíveis pelo link direto (não tinha dono pra checar).
    Pedidos novos só podem ser vistos por quem estiver logado na conta que
    fez a compra."""
    dono = pedido["cliente_id"]
    return dono is None or dono == session.get("cliente_id")


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


def login_bloqueado(chave):
    """`chave` identifica o "balde" de tentativas -- ex: f"admin:{ip}" ou
    f"cliente:{ip}" -- pra login de admin e de cliente não competirem pelo
    mesmo limite."""
    agora = time.time()
    tentativas = [t for t in _LOGIN_TENTATIVAS.get(chave, []) if agora - t < LOGIN_JANELA_SEGUNDOS]
    _LOGIN_TENTATIVAS[chave] = tentativas
    return len(tentativas) >= LOGIN_MAX_TENTATIVAS


def registrar_falha_login(chave):
    _LOGIN_TENTATIVAS.setdefault(chave, []).append(time.time())


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
    vendedor_nome = None
    if session.get("admin_logado"):
        conn = get_db()
        vendedor_nome = get_configs(conn)["vendedor_nome"]
        conn.close()
    return dict(
        categorias=CATEGORIAS, qtd_carrinho=qtd_carrinho, chat_auto_message=chat_auto_message,
        vendedor_nome=vendedor_nome,
    )


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
@login_cliente_obrigatorio
def checkout():
    conn = get_db()
    itens, total = carrinho_detalhado(conn)
    if not itens:
        conn.close()
        return redirect(url_for("loja"))

    cliente = buscar_cliente_por_id(conn, session["cliente_id"])

    if request.method == "POST":
        nome = texto_seguro(request.form.get("nome"), 120)
        telefone = texto_seguro(request.form.get("telefone"), 40)
        forma_pagamento = request.form.get("forma_pagamento", "pix")
        if forma_pagamento not in ("pix", "cartao", "combinar"):
            forma_pagamento = "combinar"

        if not nome or not telefone:
            flash("Preencha nome e telefone para finalizar o pedido.")
            conn.close()
            return render_template("checkout.html", itens=itens, total=total, cliente=cliente)

        linhas = [f"{i['qtd']}x {i['produto']['nome']} - R$ {i['subtotal']:.2f}".replace(".", ",") for i in itens]
        detalhes = "\n".join(linhas)

        cliente_lat = ler_coordenada_formulario(request.form.get("cliente_lat"))
        cliente_lng = ler_coordenada_formulario(request.form.get("cliente_lng"))

        pedido_id = criar_pedido(
            conn, "loja", detalhes, total, nome, telefone, forma_pagamento,
            cliente_id=session["cliente_id"], cliente_lat=cliente_lat, cliente_lng=cliente_lng,
        )
        # Já entra na fila de despacho pra impressora parceira mais
        # próxima (se o cliente permitiu compartilhar a localização).
        distribuicao.despachar_pedido(conn, pedido_id)
        conn.close()

        session["carrinho"] = {}
        session.modified = True

        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    conn.close()
    return render_template("checkout.html", itens=itens, total=total, cliente=cliente)


@app.route("/pedido/<int:pedido_id>/pagamento")
def pedido_pagamento(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return redirect(url_for("home"))
    if not pedido_pertence_ao_usuario(pedido):
        conn.close()
        abort(403)

    # Avança a fila de despacho antes de mostrar a tela (expira oferta
    # vencida / tenta a próxima impressora), pra página sempre refletir o
    # estado mais atual sem precisar de um processo rodando em segundo plano.
    distribuicao.avancar_distribuicao(conn, pedido_id)
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    impressora = buscar_impressora_por_id(conn, pedido["impressora_id"]) if pedido["impressora_id"] else None
    config = get_configs(conn)
    conn.close()

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
        impressora=impressora,
    )


@app.route("/pedido/<int:pedido_id>/pagar-cartao")
def pedido_pagar_cartao(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)
    if not pedido or not config["mp_access_token"].strip():
        conn.close()
        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))
    if not pedido_pertence_ao_usuario(pedido):
        conn.close()
        abort(403)

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

    if pedido and not pedido_pertence_ao_usuario(pedido):
        conn.close()
        abort(403)

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
    pedido = conn.execute("SELECT valor_estimado, cliente_id FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)
    conn.close()
    if not pedido or not config["pix_chave"].strip():
        return "", 404
    if not pedido_pertence_ao_usuario(pedido):
        abort(403)

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
    if pedido and not pedido_pertence_ao_usuario(pedido):
        conn.close()
        abort(403)
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

    cliente_logado = None
    if session.get("cliente_id"):
        conn = get_db()
        cliente_logado = buscar_cliente_por_id(conn, session["cliente_id"])
        conn.close()

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
                complexidade_js=json.dumps(COMPLEXIDADE), cliente_logado=cliente_logado,
                regra_js=json.dumps({"hora_maquina": PRECO_HORA_IMPRESSAO, "shell_fraction": SHELL_FRACTION, "cat_acabamento": CAT_ACABAMENTO}),
            )
        resultado = calcular_orcamento(
            form["altura"], form["largura"], form["profundidade"], form["quantidade"],
            form["categoria"], form["complexidade"], form["material"], form["qualidade"],
        )
        resultado["tempo_formatado"] = formatar_horas(resultado["horas_total"])

        if request.form.get("acao") == "enviar":
            if not session.get("cliente_id"):
                flash(
                    "Faça login para enviar este orçamento e acompanhá-lo em \u201cMinha "
                    "conta\u201d. Sua estimativa não foi perdida -- é só recalcular depois de entrar."
                )
                return redirect(url_for("conta_entrar", next=url_for("orcamento")))

            nome = texto_seguro(request.form.get("nome"), 120)
            telefone = texto_seguro(request.form.get("telefone"), 40)

            if not nome or not telefone:
                flash("Preencha nome e telefone para enviar o orçamento.")
                return render_template(
                    "orcamento.html", form=form, resultado=resultado,
                    materiais=MATERIAIS, qualidades=QUALIDADE, complexidades=COMPLEXIDADE,
                    materiais_js=json.dumps(MATERIAIS), qualidade_js=json.dumps(QUALIDADE),
                    complexidade_js=json.dumps(COMPLEXIDADE), cliente_logado=cliente_logado,
                    regra_js=json.dumps({"hora_maquina": PRECO_HORA_IMPRESSAO, "shell_fraction": SHELL_FRACTION, "cat_acabamento": CAT_ACABAMENTO}),
                )

            detalhes = (
                f"Categoria: {resultado['categoria_nome']}\n"
                f"Dimensões: {form['altura']}x{form['largura']}x{form['profundidade']} cm\n"
                f"Material: {resultado['material_nome']}\n"
                f"Qualidade: {form['qualidade']}\n"
                f"Quantidade: {form['quantidade']}"
            )
            cliente_lat = ler_coordenada_formulario(request.form.get("cliente_lat"))
            cliente_lng = ler_coordenada_formulario(request.form.get("cliente_lng"))
            conn = get_db()
            pedido_id = criar_pedido(
                conn, "orcamento", detalhes, resultado["preco_total"], nome, telefone, "pix",
                cliente_id=session["cliente_id"], cliente_lat=cliente_lat, cliente_lng=cliente_lng,
            )
            distribuicao.despachar_pedido(conn, pedido_id)
            conn.close()

            flash("Orçamento recebido! Você pode adiantar o pagamento por Pix ou combinar direto com a gente.")
            return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    return render_template(
        "orcamento.html", form=form, resultado=resultado,
        materiais=MATERIAIS, qualidades=QUALIDADE, complexidades=COMPLEXIDADE,
        materiais_js=json.dumps(MATERIAIS), qualidade_js=json.dumps(QUALIDADE),
        complexidade_js=json.dumps(COMPLEXIDADE), cliente_logado=cliente_logado,
        regra_js=json.dumps({"hora_maquina": PRECO_HORA_IMPRESSAO, "shell_fraction": SHELL_FRACTION, "cat_acabamento": CAT_ACABAMENTO}),
    )


# ---------- conta do cliente ----------

TELEFONE_MIN_DIGITOS = 10


@app.route("/conta/cadastro", methods=["GET", "POST"])
def conta_cadastro():
    if session.get("cliente_id"):
        return redirect(url_for("conta_dashboard"))

    if request.method == "POST":
        nome = texto_seguro(request.form.get("nome"), 120)
        telefone = normalizar_telefone(request.form.get("telefone"))
        senha = request.form.get("senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        erro = None
        if not nome:
            erro = "Preencha seu nome."
        elif len(telefone) < TELEFONE_MIN_DIGITOS:
            erro = "Informe um telefone válido, com DDD."
        elif len(senha) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."
        elif senha != confirmar_senha:
            erro = "As senhas não coincidem."

        conn = get_db()
        if not erro and buscar_cliente_por_telefone(conn, telefone):
            erro = "Já existe uma conta com esse telefone. Faça login."

        if erro:
            conn.close()
            flash(erro)
            return render_template("conta_cadastro.html")

        cliente_id = criar_cliente(conn, nome, telefone, generate_password_hash(senha))
        conn.close()

        session.clear()
        session["cliente_id"] = cliente_id
        session["cliente_nome"] = nome
        session.permanent = True
        flash("Conta criada! Bem-vindo(a).")
        return redirect(next_seguro(url_for("conta_dashboard")))

    return render_template("conta_cadastro.html")


@app.route("/conta/entrar", methods=["GET", "POST"])
def conta_entrar():
    if session.get("cliente_id"):
        return redirect(url_for("conta_dashboard"))

    erro = None
    ip = request.remote_addr or "desconhecido"
    chave_rate_limit = f"cliente:{ip}"

    if request.method == "POST":
        if login_bloqueado(chave_rate_limit):
            erro = "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."
        else:
            telefone = normalizar_telefone(request.form.get("telefone"))
            senha = request.form.get("senha", "")
            conn = get_db()
            cliente = buscar_cliente_por_telefone(conn, telefone)
            conn.close()
            if cliente and check_password_hash(cliente["senha_hash"], senha):
                session.clear()
                session["cliente_id"] = cliente["id"]
                session["cliente_nome"] = cliente["nome"]
                session.permanent = True
                return redirect(next_seguro(url_for("conta_dashboard")))
            registrar_falha_login(chave_rate_limit)
            erro = "Telefone ou senha incorretos."

    return render_template("conta_entrar.html", erro=erro)


@app.route("/conta/sair")
def conta_sair():
    session.pop("cliente_id", None)
    session.pop("cliente_nome", None)
    return redirect(url_for("home"))


@app.route("/conta")
@login_cliente_obrigatorio
def conta_dashboard():
    conn = get_db()
    pedidos = listar_pedidos_cliente(conn, session["cliente_id"])
    conn.close()
    return render_template("conta_dashboard.html", pedidos=pedidos)


# ---------- admin ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    erro = None
    ip = request.remote_addr or "desconhecido"
    chave_rate_limit = f"admin:{ip}"
    if request.method == "POST":
        if login_bloqueado(chave_rate_limit):
            erro = "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."
        elif secrets.compare_digest(request.form.get("senha", ""), ADMIN_PASSWORD):
            session.clear()
            session["admin_logado"] = True
            session.permanent = True  # expira sozinho após PERMANENT_SESSION_LIFETIME
            return redirect(url_for("admin_dashboard"))
        else:
            registrar_falha_login(chave_rate_limit)
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
    comissoes = resumo_comissoes(conn)
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
        comissao_total=comissoes["total"],
        comissao_por_impressora=comissoes["por_impressora"],
    )


@app.route("/admin/configuracoes", methods=["GET", "POST"])
@login_obrigatorio
def admin_configuracoes():
    conn = get_db()
    if request.method == "POST":
        try:
            comissao_pct = float(request.form.get("comissao_percentual", "15").replace(",", "."))
        except ValueError:
            comissao_pct = 15.0
        comissao_pct = max(0.0, min(comissao_pct, 100.0))  # nunca deixa negativo ou acima de 100%
        set_configs(conn, {
            "vendedor_nome": request.form.get("vendedor_nome", "").strip() or "Voxxel",
            "pix_chave": request.form.get("pix_chave", "").strip(),
            "pix_nome": request.form.get("pix_nome", "").strip() or "Voxxel Impressao 3D",
            "pix_cidade": request.form.get("pix_cidade", "").strip() or "Sao Jose dos Pinhais",
            "whatsapp": request.form.get("whatsapp", "").strip(),
            "mp_access_token": request.form.get("mp_access_token", "").strip(),
            "comissao_percentual": str(comissao_pct),
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

    # Avança a fila de despacho antes de montar a listagem: expira ofertas
    # vencidas dos pedidos "buscando", e dá uma segunda chance aos que
    # ficaram "sem impressora" (pode ter aparecido alguém disponível
    # desde a última tentativa). Sem worker em segundo plano, é a própria
    # visita a essa tela que "puxa" o avanço da fila.
    em_busca = conn.execute(
        "SELECT id FROM pedidos WHERE distribuicao_status = 'buscando'"
    ).fetchall()
    for row in em_busca:
        distribuicao.avancar_distribuicao(conn, row["id"])
    distribuicao.reconsiderar_pedidos_sem_impressora(conn)

    todos = conn.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    impressoras_disponiveis = conn.execute(
        "SELECT id, nome FROM impressoras WHERE ativo = 1 ORDER BY nome"
    ).fetchall()
    impressoras_por_id = {imp["id"]: imp["nome"] for imp in conn.execute("SELECT id, nome FROM impressoras").fetchall()}
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
        "admin_pedidos.html", pedidos=pedidos, status_filtro=status_filtro, contagens=contagens,
        impressoras_disponiveis=impressoras_disponiveis, impressoras_por_id=impressoras_por_id,
    )


@app.route("/admin/pedidos/<int:pedido_id>/atribuir-impressora", methods=["POST"])
@login_obrigatorio
def admin_pedido_atribuir_impressora(pedido_id):
    """Válvula de escape manual: usada quando ninguém aceitou
    automaticamente (ou pra forçar uma impressora específica), sem
    depender da fila de ofertas."""
    try:
        impressora_id = int(request.form.get("impressora_id", ""))
    except (TypeError, ValueError):
        flash("Selecione uma impressora válida.")
        return redirect(url_for("admin_pedidos"))

    conn = get_db()
    pedido = conn.execute("SELECT id FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    impressora = buscar_impressora_por_id(conn, impressora_id)
    if not pedido:
        conn.close()
        flash("Pedido não encontrado.")
        return redirect(url_for("admin_pedidos"))
    if not impressora or not impressora["ativo"]:
        conn.close()
        flash("Essa impressora não está disponível pra receber pedidos.")
        return redirect(url_for("admin_pedidos"))

    distribuicao.atribuir_manualmente(conn, pedido_id, impressora_id)
    conn.close()
    flash(f"Pedido atribuído manualmente a {impressora['nome']}.")
    return redirect(url_for("admin_pedidos"))


@app.route("/admin/impressoras")
@login_obrigatorio
def admin_impressoras():
    conn = get_db()
    impressoras = listar_impressoras(conn)
    conn.close()
    return render_template("admin_impressoras.html", impressoras=impressoras)


@app.route("/admin/impressoras/<int:impressora_id>/toggle", methods=["POST"])
@login_obrigatorio
def admin_impressora_toggle(impressora_id):
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, impressora_id)
    if impressora:
        definir_impressora_ativa(conn, impressora_id, not impressora["ativo"])
    conn.close()
    return redirect(url_for("admin_impressoras"))


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


# ---------- painel da impressora parceira (marketplace) ----------

TELEFONE_MIN_DIGITOS_IMPRESSORA = 10


@app.route("/impressora/cadastro", methods=["GET", "POST"])
def impressora_cadastro():
    if session.get("impressora_id"):
        return redirect(url_for("impressora_painel"))

    if request.method == "POST":
        nome = texto_seguro(request.form.get("nome"), 120)
        telefone = normalizar_telefone(request.form.get("telefone"))
        senha = request.form.get("senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        erro = None
        if not nome:
            erro = "Preencha seu nome (ou o nome da sua impressora/oficina)."
        elif len(telefone) < TELEFONE_MIN_DIGITOS_IMPRESSORA:
            erro = "Informe um telefone válido, com DDD."
        elif len(senha) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."
        elif senha != confirmar_senha:
            erro = "As senhas não coincidem."

        conn = get_db()
        if not erro and buscar_impressora_por_telefone(conn, telefone):
            erro = "Já existe uma impressora cadastrada com esse telefone. Faça login."

        if erro:
            conn.close()
            flash(erro)
            return render_template("impressora_cadastro.html")

        impressora_id = criar_impressora(conn, nome, telefone, generate_password_hash(senha))
        conn.close()

        session.clear()
        session["impressora_id"] = impressora_id
        session["impressora_nome"] = nome
        session.permanent = True
        flash("Cadastro feito! Agora é só ficar online no painel pra começar a receber pedidos.")
        return redirect(url_for("impressora_painel"))

    return render_template("impressora_cadastro.html")


@app.route("/impressora/entrar", methods=["GET", "POST"])
def impressora_entrar():
    if session.get("impressora_id"):
        return redirect(url_for("impressora_painel"))

    erro = None
    ip = request.remote_addr or "desconhecido"
    chave_rate_limit = f"impressora:{ip}"

    if request.method == "POST":
        if login_bloqueado(chave_rate_limit):
            erro = "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."
        else:
            telefone = normalizar_telefone(request.form.get("telefone"))
            senha = request.form.get("senha", "")
            conn = get_db()
            impressora = buscar_impressora_por_telefone(conn, telefone)
            conn.close()
            if impressora and check_password_hash(impressora["senha_hash"], senha):
                session.clear()
                session["impressora_id"] = impressora["id"]
                session["impressora_nome"] = impressora["nome"]
                session.permanent = True
                return redirect(next_seguro(url_for("impressora_painel")))
            registrar_falha_login(chave_rate_limit)
            erro = "Telefone ou senha incorretos."

    return render_template("impressora_entrar.html", erro=erro)


@app.route("/impressora/sair")
def impressora_sair():
    session.pop("impressora_id", None)
    session.pop("impressora_nome", None)
    return redirect(url_for("home"))


@app.route("/impressora/painel")
@login_impressora_obrigatorio
def impressora_painel():
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    if not impressora:
        conn.close()
        session.clear()
        return redirect(url_for("impressora_entrar"))

    oferta = distribuicao.oferta_pendente_da_impressora(conn, impressora["id"])
    oferta_pedido = None
    oferta_distancia_km = None
    oferta_segundos_restantes = None
    oferta_ganho_estimado = None
    pct_comissao = percentual_comissao(conn)
    if oferta:
        oferta_pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (oferta["pedido_id"],)).fetchone()
        oferta_segundos_restantes = distribuicao.segundos_restantes_oferta(oferta)
        if oferta_pedido and impressora["latitude"] is not None:
            oferta_distancia_km = round(
                distribuicao.haversine_km(
                    impressora["latitude"], impressora["longitude"],
                    oferta_pedido["cliente_lat"], oferta_pedido["cliente_lng"],
                ),
                1,
            )
        if oferta_pedido:
            # Quanto a impressora efetivamente embolsa se aceitar -- valor
            # do pedido já descontada a comissão da Voxxel, pra ela decidir
            # com o número certo na mão, não o valor bruto do pedido.
            oferta_ganho_estimado = round(oferta_pedido["valor_estimado"] * (1 - pct_comissao / 100), 2)

    pedidos_atribuidos = listar_pedidos_da_impressora(conn, impressora["id"])
    ganho_acumulado = sum(
        (p["valor_estimado"] - (p["comissao_voxxel"] or 0)) for p in pedidos_atribuidos
    )
    conn.close()

    return render_template(
        "impressora_painel.html", impressora=impressora, oferta=oferta, oferta_pedido=oferta_pedido,
        oferta_distancia_km=oferta_distancia_km, oferta_segundos_restantes=oferta_segundos_restantes,
        oferta_ganho_estimado=oferta_ganho_estimado, pedidos=pedidos_atribuidos,
        ganho_acumulado=round(ganho_acumulado, 2), pct_comissao=pct_comissao,
    )


@app.route("/impressora/status", methods=["POST"])
@login_impressora_obrigatorio
def impressora_status():
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    if not impressora or not impressora["ativo"]:
        conn.close()
        flash("Sua conta de impressora parceira está bloqueada. Fale com a Voxxel.")
        return redirect(url_for("impressora_painel"))

    online = request.form.get("online") == "1"
    latitude = ler_coordenada_formulario(request.form.get("latitude"))
    longitude = ler_coordenada_formulario(request.form.get("longitude"))
    if online and (latitude is None or longitude is None):
        conn.close()
        flash("Precisamos da sua localização pra te colocar online -- permita o acesso à localização no navegador.")
        return redirect(url_for("impressora_painel"))

    definir_status_impressora(conn, session["impressora_id"], online, latitude, longitude)
    if online:
        # Impressora acabou de ficar disponível: vale a pena reconsiderar
        # pedidos que tinham ficado "sem impressora" -- talvez ela seja a
        # primeira opção disponível pra algum deles agora.
        distribuicao.reconsiderar_pedidos_sem_impressora(conn)
    conn.close()
    return redirect(url_for("impressora_painel"))


@app.route("/impressora/localizacao", methods=["POST"])
@login_impressora_obrigatorio
def impressora_localizacao():
    """Ping em segundo plano (AJAX) enviado pelo painel enquanto a
    impressora está online, pra manter a posição sempre atualizada."""
    latitude = ler_coordenada_formulario(request.form.get("latitude"))
    longitude = ler_coordenada_formulario(request.form.get("longitude"))
    if latitude is None or longitude is None:
        return {"ok": False}, 400
    conn = get_db()
    atualizar_localizacao_impressora(conn, session["impressora_id"], latitude, longitude)
    conn.close()
    return {"ok": True}


@app.route("/impressora/oferta/<int:oferta_id>/responder", methods=["POST"])
@login_impressora_obrigatorio
def impressora_oferta_responder(oferta_id):
    acao = request.form.get("acao")
    if acao not in ("aceitar", "recusar"):
        abort(400)
    conn = get_db()
    aplicado = distribuicao.responder_oferta(conn, oferta_id, session["impressora_id"], acao == "aceitar")
    conn.close()
    if not aplicado:
        flash("Essa oferta não está mais disponível (talvez já tenha expirado).")
    elif acao == "aceitar":
        flash("Pedido aceito! Já apareceu na sua lista de impressões.")
    else:
        flash("Oferta recusada. Ela foi repassada pra próxima impressora mais próxima.")
    return redirect(url_for("impressora_painel"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Por padrão, debug fica DESLIGADO -- precisa ligar explicitamente em
    # desenvolvimento. Rodar com o debugger do Werkzeug ligado em produção é
    # uma falha grave de segurança: ele permite executar código arbitrário
    # no servidor pra quem encontrar uma página de erro.
    debug = os.environ.get("VOXXEL_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
