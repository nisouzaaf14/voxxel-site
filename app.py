import os
import io
import json
import time
import math
import secrets
import zipfile
from datetime import timedelta
from urllib.parse import urlparse
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, g, Response, abort, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image

from database import (
    init_db, get_db, CATEGORIAS, to_blob, criar_pedido, get_configs, set_configs, USING_POSTGRES,
    normalizar_telefone, criar_cliente, buscar_cliente_por_telefone, buscar_cliente_por_id,
    listar_pedidos_cliente, ler_coordenada_formulario,
    criar_impressora, buscar_impressora_por_telefone, buscar_impressora_por_id, listar_impressoras,
    definir_status_impressora, atualizar_localizacao_impressora, atualizar_materiais_impressora, definir_impressora_ativa,
    listar_pedidos_da_impressora, resumo_comissoes, percentual_comissao, aplicar_comissao_pedido,
)
from calculadora import (
    calcular_orcamento, formatar_horas, MATERIAIS, QUALIDADE, COMPLEXIDADE,
    SHELL_FRACTION, CAT_ACABAMENTO, CAT_PREPARACAO, regras_precificacao, materiais_com_preco,
)
import pix
import mercadopago_pay
import distribuicao

SECRET_KEY_ENV = os.environ.get("VOXXEL_SECRET_KEY", "").strip()
ADMIN_PASSWORD = os.environ.get("VOXXEL_ADMIN_PASSWORD", "").strip()
CHAT_WEBHOOK_URL = os.environ.get("VOXXEL_CHAT_WEBHOOK_URL", "").strip()

# Nunca mantenha credenciais reais no repositório. Em produção, defina
# VOXXEL_SECRET_KEY e VOXXEL_ADMIN_PASSWORD nas variáveis de ambiente.
# Sem SECRET_KEY configurada, uma chave efêmera é usada apenas para permitir
# execução local; sessões serão invalidadas quando o processo reiniciar.
SECRET_KEY = SECRET_KEY_ENV or secrets.token_hex(32)

TIPOS_IMAGEM_PERMITIDOS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

EXTENSOES_MODELO_PERMITIDAS = {"stl", "3mf", "obj"}
MAX_MODELO_BYTES = 15 * 1024 * 1024
MAX_IMAGEM_REFERENCIA_BYTES = 6 * 1024 * 1024
MAX_ANEXO_CHAT_BYTES = 12 * 1024 * 1024

FLUXO_LABELS = {
    "recebido": "Pedido recebido",
    "em_analise": "Em análise pelo parceiro",
    "precisa_info": "Precisamos de informações",
    "cliente_respondeu": "Cliente respondeu",
    "aguardando_aprovacao": "Aguardando sua aprovação",
    "producao_autorizada": "Produção autorizada",
    "em_producao": "Em produção",
    "pronto": "Pronto",
    "concluido": "Concluído",
    "cancelado": "Cancelado",
}

app = Flask(__name__)

# Em produção não é seguro assinar sessões com uma chave efêmera: cada
# reinício invalidaria logins e, pior, um deploy mal configurado pareceria
# saudável. Localmente ainda aceitamos a chave temporária para facilitar o
# desenvolvimento, mas em PaaS/produção a configuração passa a ser
# obrigatória (fail closed).
EM_PRODUCAO = bool(os.environ.get("PORT") or os.environ.get("VOXXEL_PRODUCTION") == "1")
if EM_PRODUCAO and not SECRET_KEY_ENV:
    raise RuntimeError("VOXXEL_SECRET_KEY é obrigatória em produção.")
app.secret_key = SECRET_KEY

# ProxyFix só deve confiar em X-Forwarded-* quando sabemos que a aplicação
# está atrás de um proxy reverso confiável. Confiar nesses headers em um
# servidor exposto diretamente permitiria falsificar IP/esquema/host.
CONFIAR_PROXY = os.environ.get(
    "VOXXEL_TRUST_PROXY", "true" if os.environ.get("PORT") else "false"
).lower() == "true"
if CONFIAR_PROXY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    MAX_CONTENT_LENGTH=35 * 1024 * 1024,  # referências do projeto: imagens + arquivo 3D
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

if not SECRET_KEY_ENV:
    print(
        "[AVISO DE SEGURANÇA] VOXXEL_SECRET_KEY não foi definida. Uma chave efêmera "
        "foi criada para esta execução. Configure uma chave longa e aleatória em produção."
    )
if not ADMIN_PASSWORD:
    print(
        "[AVISO DE SEGURANÇA] VOXXEL_ADMIN_PASSWORD não foi definida. O login do painel "
        "administrativo ficará desabilitado até a variável ser configurada."
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
            flash("Faça login para acessar a área do parceiro.")
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
    """Restringe dados financeiros do pedido ao cliente dono ou ao admin.

    Pedidos legados sem cliente vinculado não ficam públicos por URL previsível;
    o administrador continua podendo acessá-los pelo painel.
    """
    if session.get("admin_logado"):
        return True
    dono = pedido["cliente_id"]
    return dono is not None and dono == session.get("cliente_id")


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


@app.errorhandler(403)
def erro_proibido(e):
    return render_template(
        "erro.html", codigo="403", titulo="Acesso não permitido",
        mensagem="Você não tem permissão para acessar esta área com a conta atual.",
    ), 403


@app.errorhandler(404)
def erro_nao_encontrado(e):
    return render_template(
        "erro.html", codigo="404", titulo="Página não encontrada",
        mensagem="Este endereço não existe mais ou foi digitado incorretamente.",
    ), 404


@app.errorhandler(500)
def erro_interno(e):
    # Nunca expõe stack trace ou detalhes internos ao visitante.
    return render_template(
        "erro.html", codigo="500", titulo="Algo não saiu como esperado",
        mensagem="Ocorreu um erro interno. Tente novamente em instantes.",
    ), 500


@app.after_request
def adicionar_headers_seguranca(resposta):
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resposta.headers["Permissions-Policy"] = "geolocation=(self), microphone=(), camera=()"
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
        "frame-src 'none'; frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )
    resposta.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resposta.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resposta.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if request.path.startswith(("/pedido/", "/conta", "/impressora/", "/admin", "/checkout", "/carrinho")):
        resposta.headers["Cache-Control"] = "no-store, private"
        resposta.headers["Pragma"] = "no-cache"
        resposta.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.is_secure:
        resposta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resposta


@app.errorhandler(413)
def imagem_grande_demais(e):
    flash("Os arquivos enviados ultrapassaram o limite do pedido. Envie referências menores ou em menos arquivos.")
    return redirect(redirecionamento_seguro(url_for("home")))


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
    # Evita crescimento indefinido em processo longo sob muitos IPs únicos.
    if len(_LOGIN_TENTATIVAS) > 5000:
        agora = time.time()
        for k in list(_LOGIN_TENTATIVAS)[:1000]:
            recentes = [t for t in _LOGIN_TENTATIVAS[k] if agora - t < LOGIN_JANELA_SEGUNDOS]
            if recentes:
                _LOGIN_TENTATIVAS[k] = recentes
            else:
                _LOGIN_TENTATIVAS.pop(k, None)


def limpar_falhas_login(chave):
    _LOGIN_TENTATIVAS.pop(chave, None)


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
    itens, total, atualizado = [], 0.0, {}
    for pid, qtd in list(carrinho_sessao().items()):
        row = conn.execute(
            f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE id = ? AND ativo = 1", (int(pid),)
        ).fetchone()
        if not row or (row["estoque"] is not None and row["estoque"] <= 0):
            continue
        qtd = max(1, min(int(qtd), 999))
        if row["estoque"] is not None:
            qtd = min(qtd, row["estoque"])
        atualizado[pid] = qtd
        subtotal = row["preco"] * qtd
        total += subtotal
        itens.append({"produto": row, "qtd": qtd, "subtotal": subtotal})
    if atualizado != carrinho_sessao():
        session["carrinho"] = atualizado
        session.modified = True
        flash("Atualizamos seu carrinho conforme a disponibilidade. Confira os itens e o subtotal.")
    return itens, total


@app.context_processor
def inject_globals():
    qtd_carrinho = sum(carrinho_sessao().values())
    chat_auto_message = session.pop("voxxel_chat_auto", None)
    # O layout base também é usado nas páginas de erro. Se o banco estiver
    # indisponível, o próprio fallback de erro precisa conseguir renderizar.
    config = {"vendedor_nome": "Voxxel", "whatsapp": ""}
    conn = None
    try:
        conn = get_db()
        config = get_configs(conn)
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    vendedor_nome = config.get("vendedor_nome", "Voxxel") if session.get("admin_logado") else None
    whatsapp_numero = "".join(c for c in config.get("whatsapp", "") if c.isdigit())
    chat_webhook_url = ""
    if CHAT_WEBHOOK_URL:
        parsed_chat = urlparse(CHAT_WEBHOOK_URL)
        if parsed_chat.scheme in ("https", "http") and parsed_chat.netloc:
            chat_webhook_url = CHAT_WEBHOOK_URL
    return dict(
        categorias=CATEGORIAS, materiais_catalogo=MATERIAIS, qtd_carrinho=qtd_carrinho, chat_auto_message=chat_auto_message,
        vendedor_nome=vendedor_nome, whatsapp_numero=whatsapp_numero, chat_webhook_url=chat_webhook_url,
    )


# ---------- páginas públicas ----------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/termos")
def termos():
    return render_template("institucional.html", pagina="termos")


@app.route("/privacidade")
def privacidade():
    return render_template("institucional.html", pagina="privacidade")


@app.route("/health")
def health():
    """Health check simples para Render/monitoramento, incluindo o banco."""
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return {"ok": True, "database": "postgres" if USING_POSTGRES else "sqlite"}
    except Exception:
        return {"ok": False}, 503


# Colunas usadas nas listagens (loja e admin): evita carregar o BLOB da
# imagem inteiro só pra mostrar a lista de produtos -- só um indicador
# booleano (tem_imagem) que a rota /produto/<id>/imagem resolve de verdade.
COLUNAS_PRODUTO_LISTA = """
    id, nome, categoria, preco, descricao, imagem_ang, ativo, estoque, material,
    (imagem_mimetype IS NOT NULL) AS tem_imagem
"""


@app.route("/loja")
def loja():
    """Catálogo público com busca, categoria, ordenação e paginação no servidor.

    Os filtros são validados por allowlist e os valores do usuário sempre entram
    como parâmetros SQL, nunca como trechos de consulta.
    """
    conn = get_db()
    categoria = request.args.get("categoria", "todos").strip().lower()
    if categoria != "todos" and categoria not in CATEGORIAS:
        categoria = "todos"
    busca = texto_seguro(request.args.get("q"), 80)
    ordem = request.args.get("ordem", "recentes").strip().lower()
    ordenacoes = {
        "recentes": "id DESC",
        "preco_menor": "preco ASC, id DESC",
        "preco_maior": "preco DESC, id DESC",
        "nome": "nome ASC, id DESC",
    }
    if ordem not in ordenacoes:
        ordem = "recentes"
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except (TypeError, ValueError):
        pagina = 1
    por_pagina = 12

    filtros = ["ativo = 1"]
    params = []
    if categoria != "todos":
        filtros.append("categoria = ?")
        params.append(categoria)
    if busca:
        filtros.append("(LOWER(nome) LIKE ? OR LOWER(descricao) LIKE ?)")
        termo = f"%{busca.lower()}%"
        params.extend([termo, termo])
    where = " AND ".join(filtros)
    total = conn.execute(f"SELECT COUNT(*) AS total FROM produtos WHERE {where}", tuple(params)).fetchone()["total"]
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * por_pagina
    produtos = conn.execute(
        f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE {where} ORDER BY {ordenacoes[ordem]} LIMIT ? OFFSET ?",
        tuple(params + [por_pagina, offset]),
    ).fetchall()
    conn.close()
    return render_template(
        "loja.html", produtos=produtos, categoria_ativa=categoria, busca=busca, ordem=ordem,
        pagina=pagina, total_paginas=total_paginas, total_produtos=total,
    )


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
    nova_qtd = min(999, carrinho.get(pid, 0) + quantidade_pedida)

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


@app.route("/carrinho/atualizar/<int:produto_id>", methods=["POST"])
def carrinho_atualizar(produto_id):
    try:
        quantidade = int(request.form.get("quantidade", ""))
        if not 1 <= quantidade <= 999:
            raise ValueError
    except ValueError:
        flash("Informe uma quantidade entre 1 e 999.")
        return redirect(url_for("carrinho"))
    carrinho = carrinho_sessao()
    if str(produto_id) in carrinho:
        carrinho[str(produto_id)] = quantidade
        session.modified = True
        conn = get_db()
        carrinho_detalhado(conn)
        conn.close()
    return redirect(url_for("carrinho"))


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
    carrinho_anterior = dict(carrinho_sessao())
    itens, total = carrinho_detalhado(conn)
    if request.method == "POST" and carrinho_anterior != carrinho_sessao():
        conn.close()
        return redirect(url_for("carrinho"))
    if not itens:
        conn.close()
        return redirect(url_for("loja"))

    cliente = buscar_cliente_por_id(conn, session["cliente_id"])
    config = get_configs(conn)
    pagamentos = {"combinar": "Definir após confirmação"}
    if pix.chave_valida(config.get("pix_chave", "")):
        pagamentos["pix"] = "Pix"
    if config["mp_access_token"].strip():
        pagamentos["cartao"] = "Cartão"

    if request.method == "POST":
        nome = texto_seguro(request.form.get("nome"), 120)
        telefone = texto_seguro(request.form.get("telefone"), 40)
        forma_pagamento = request.form.get("forma_pagamento", "combinar")
        if forma_pagamento not in pagamentos:
            forma_pagamento = "combinar"

        recebimento = request.form.get("recebimento", "combinar")
        if recebimento not in ("combinar", "retirada", "entrega"):
            recebimento = "combinar"
        endereco = texto_seguro(request.form.get("endereco"), 350)
        observacoes = texto_seguro(request.form.get("observacoes"), 500)
        telefone_digitos = "".join(c for c in telefone if c.isdigit())
        if recebimento != "retirada":
            forma_pagamento = "combinar"
        if not nome or not 10 <= len(telefone_digitos) <= 13 or (recebimento == "entrega" and len(endereco) < 15):
            flash("Confira seu nome, telefone com DDD e, para entrega, o endereço completo com CEP.")
            conn.close()
            return render_template("checkout.html", itens=itens, total=total, cliente=cliente, pagamentos=pagamentos)

        linhas = [
            (f"{i['qtd']}x {i['produto']['nome']} · "
             f"{MATERIAIS.get(i['produto']['material'], {}).get('nome', i['produto']['material'].upper())} - "
             f"R$ {i['subtotal']:.2f}").replace(".", ",")
            for i in itens
        ]
        nomes_recebimento = {"combinar": "A combinar após confirmação", "retirada": "Retirada — local e horário a confirmar", "entrega": "Entrega — frete e prazo a confirmar antes do pagamento"}
        linhas.append("Recebimento: " + nomes_recebimento[recebimento])
        if recebimento == "entrega":
            linhas.append("Endereço: " + endereco)
        if observacoes:
            linhas.append("Observações: " + observacoes)
        detalhes = "\n".join(linhas)

        cliente_lat = ler_coordenada_formulario(request.form.get("cliente_lat"), -90, 90)
        cliente_lng = ler_coordenada_formulario(request.form.get("cliente_lng"), -180, 180)

        materiais_pedido = sorted({
            (i["produto"]["material"] or "pla").strip().lower() for i in itens
        })
        # O pedido + snapshot dos itens + reserva de estoque formam uma só
        # transação. Se outra compra levar a última unidade entre a tela e
        # este POST, a atualização condicional falha e nada é gravado.
        pedido_id = criar_pedido(
            conn, "loja", detalhes, total, nome, telefone, forma_pagamento,
            cliente_id=session["cliente_id"], cliente_lat=cliente_lat, cliente_lng=cliente_lng,
            material_requisito=",".join(materiais_pedido), commit=False,
        )
        estoque_ok = True
        for item in itens:
            produto = item["produto"]
            if produto["estoque"] is not None:
                cur = conn.execute(
                    "UPDATE produtos SET estoque = estoque - ? WHERE id = ? AND ativo = 1 AND estoque >= ?",
                    (item["qtd"], produto["id"], item["qtd"]),
                )
                if cur.rowcount != 1:
                    estoque_ok = False
                    break
            conn.execute(
                """INSERT INTO pedido_itens
                   (pedido_id, produto_id, nome, quantidade, preco_unitario, subtotal, material)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pedido_id, produto["id"], produto["nome"], item["qtd"], produto["preco"],
                 item["subtotal"], produto["material"] or "pla"),
            )
        if not estoque_ok:
            conn.rollback()
            conn.close()
            flash("O estoque mudou enquanto você finalizava. Revise o carrinho antes de continuar.")
            return redirect(url_for("carrinho"))
        conn.commit()

        # Só depois da compra estar persistida ela entra na fila de despacho.
        distribuicao.despachar_pedido(conn, pedido_id)
        conn.close()

        session["carrinho"] = {}
        session.modified = True

        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))

    conn.close()
    return render_template("checkout.html", itens=itens, total=total, cliente=cliente, pagamentos=pagamentos)


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

    pagamento_liberado = pedido["status"] != "cancelado" and (pedido["tipo"] != "orcamento" or bool(pedido["producao_autorizada"]))
    pix_disponivel = pagamento_liberado and pix.chave_valida(config.get("pix_chave", "")) and pedido["forma_pagamento"] == "pix"
    cartao_disponivel = pagamento_liberado and bool(config["mp_access_token"].strip()) and pedido["forma_pagamento"] == "cartao"
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


@app.route("/pedido/<int:pedido_id>/pagar-cartao", methods=["POST"])
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
    if pedido["status"] == "cancelado" or pedido["forma_pagamento"] != "cartao" or pedido["status_pagamento"] == "confirmado":
        conn.close()
        flash("Esse pedido não está disponível para pagamento por cartão.")
        return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))
    if pedido["tipo"] == "orcamento" and not pedido["producao_autorizada"]:
        conn.close()
        flash("O pagamento do projeto é liberado depois da sua aprovação final.")
        return redirect(url_for("pedido_projeto", pedido_id=pedido_id))

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
        payment_id = str(payment_id or "").strip()
        if not payment_id.isdigit() or len(payment_id) > 32:
            payment_id = ""
        status = None
        dados_pagamento = {}
        if payment_id:
            try:
                dados_pagamento = mercadopago_pay.consultar_pagamento(config["mp_access_token"], payment_id)
                status = dados_pagamento.get("status")
            except Exception:
                status = None
        pagamento_valido = status == "approved" and mercadopago_pay.pagamento_confere_com_pedido(
            dados_pagamento, pedido_id, pedido["valor_estimado"]
        ) if payment_id else False
        if pagamento_valido:
            conn.execute(
                "UPDATE pedidos SET status_pagamento = 'confirmado', mp_payment_id = ? WHERE id = ?",
                (str(payment_id), pedido_id),
            )
            conn.commit()
            msg = (
                f"Olá! Acabei de pagar com cartão o pedido #{pedido_id} no site da Voxxel.\n\n"
                f"{pedido['detalhes']}\n\nTotal: R$ {pedido['valor_estimado']:.2f}".replace(".", ",")
            )
            session["voxxel_chat_auto"] = msg
            session.modified = True
            flash("Pagamento aprovado e confirmado pelo Mercado Pago. Você pode acompanhar os próximos passos na área do cliente.")
        elif status == "pending" or request.args.get("status") == "pending":
            flash("Pagamento em análise. Assim que for aprovado, atualizamos seu pedido.")
        else:
            if status in ("rejected", "cancelled"):
                conn.execute("UPDATE pedidos SET status_pagamento='recusado' WHERE id=? AND status_pagamento<>'confirmado'", (pedido_id,))
                conn.commit()
            flash("O pagamento não foi concluído. Você pode tentar novamente.")
    conn.close()
    return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))


@app.route("/webhooks/mercadopago", methods=["POST", "GET"])
def webhook_mercadopago():
    """Notificação automática do Mercado Pago (IPN). É a forma confiável de
    saber que um pagamento por cartão foi aprovado, mesmo se o cliente
    fechar a aba antes de voltar pro site."""
    payment_id = request.args.get("data.id") or (request.get_json(silent=True) or {}).get("data", {}).get("id")
    payment_id = str(payment_id or "").strip()
    if not payment_id.isdigit() or len(payment_id) > 32:
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
    if pedido_id:
        try:
            pedido_id = int(pedido_id)
        except (TypeError, ValueError):
            conn.close()
            return "", 200
        pedido = conn.execute("SELECT id, valor_estimado FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if pedido and mercadopago_pay.pagamento_confere_com_pedido(dados_pagamento, pedido_id, pedido["valor_estimado"]):
            conn.execute(
                "UPDATE pedidos SET status_pagamento='confirmado', mp_payment_id=? WHERE id=? AND status_pagamento<>'confirmado'",
                (str(payment_id), pedido_id),
            )
            conn.commit()
    conn.close()
    return "", 200


@app.route("/pedido/<int:pedido_id>/pix.png")
def pedido_pix_png(pedido_id):
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    config = get_configs(conn)
    conn.close()
    if not pedido or not pix.chave_valida(config.get("pix_chave", "")):
        return "", 404
    if not pedido_pertence_ao_usuario(pedido):
        abort(403)
    if (pedido["status"] == "cancelado" or pedido["forma_pagamento"] != "pix" or
            (pedido["tipo"] == "orcamento" and not pedido["producao_autorizada"])):
        abort(404)

    try:
        payload = pix.gerar_payload(
            config["pix_chave"], config["pix_nome"], config["pix_cidade"],
            pedido["valor_estimado"], txid=f"VOXXEL{pedido_id}",
        )
    except ValueError:
        abort(404)
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
        if pedido["status"] == "cancelado" or pedido["forma_pagamento"] != "pix" or pedido["status_pagamento"] == "confirmado":
            conn.close()
            flash("Esse pedido não está aguardando confirmação de Pix.")
            return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))
        if pedido["tipo"] == "orcamento" and not pedido["producao_autorizada"]:
            conn.close()
            flash("Aguarde a aprovação final do projeto antes de realizar o pagamento.")
            return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
        conn.execute("UPDATE pedidos SET status_pagamento = 'informado' WHERE id = ?", (pedido_id,))
        conn.commit()

        linhas = pedido["detalhes"]
        msg = (
            f"Olá! Acabei de fazer o pagamento do pedido #{pedido_id} no site da Voxxel.\n\n"
            f"{linhas}\n\n"
            f"Total: R$ {pedido['valor_estimado']:.2f}".replace(".", ",")
        )
        session["voxxel_chat_auto"] = msg
        session.modified = True
        flash("Pagamento informado. Aguarde a conferência da Voxxel.")
    conn.close()
    return redirect(url_for("pedido_pagamento", pedido_id=pedido_id))


def _extensao(nome):
    nome = secure_filename(nome or "")
    return nome.rsplit(".", 1)[-1].lower() if "." in nome else ""


def validar_modelo_3d(arquivo, limite=MAX_MODELO_BYTES):
    """Valida referências 3D sem executar/processar geometria no servidor."""
    if not arquivo or not arquivo.filename:
        return None
    nome = secure_filename(arquivo.filename)[:180]
    ext = _extensao(nome)
    if ext not in EXTENSOES_MODELO_PERMITIDAS:
        raise ValueError("Arquivo 3D inválido. Envie STL, 3MF ou OBJ.")
    dados = arquivo.read(limite + 1)
    if not dados:
        raise ValueError("O arquivo 3D enviado está vazio.")
    if len(dados) > limite:
        raise ValueError("O arquivo 3D é grande demais. O limite é 15 MB por arquivo.")
    if ext == "3mf":
        try:
            with zipfile.ZipFile(io.BytesIO(dados)) as zf:
                infos = zf.infolist()
                if len(infos) > 5000 or sum(i.file_size for i in infos) > 200 * 1024 * 1024:
                    raise ValueError
                nomes = {i.filename.lower() for i in infos}
                if "[content_types].xml" not in nomes or not any(n.startswith("3d/") for n in nomes):
                    raise ValueError
        except Exception:
            raise ValueError("O arquivo 3MF parece corrompido ou inválido.")
    elif ext == "obj":
        amostra = dados[:200000].decode("utf-8", errors="ignore")
        if "\nv " not in "\n" + amostra and "\no " not in "\n" + amostra:
            raise ValueError("O arquivo OBJ não parece conter uma malha 3D válida.")
    elif ext == "stl" and len(dados) < 84 and not dados.lstrip().lower().startswith(b"solid"):
        raise ValueError("O arquivo STL parece incompleto.")
    mimetype = {"stl": "model/stl", "3mf": "model/3mf", "obj": "text/plain"}[ext]
    return {"nome": nome, "mimetype": mimetype, "dados": dados}


def validar_imagem_referencia(arquivo, limite=MAX_IMAGEM_REFERENCIA_BYTES):
    if not arquivo or not arquivo.filename:
        return None
    dados = arquivo.read(limite + 1)
    if not dados:
        return None
    if len(dados) > limite:
        raise ValueError("Cada imagem de referência pode ter no máximo 6 MB.")
    try:
        dados_webp, mimetype = _imagem_webp_segura(dados, max_dim=1800, qualidade=84)
    except ValueError:
        raise ValueError("Uma das referências não é uma imagem válida. Use JPG, PNG ou WEBP.")
    stem = os.path.splitext(secure_filename(arquivo.filename)[:170] or "referencia")[0]
    return {"nome": f"{stem}.webp", "mimetype": mimetype, "dados": dados_webp}


def inserir_referencia(conn, pedido_id, tipo, arquivo_info):
    conn.execute(
        """INSERT INTO pedido_referencias (pedido_id, tipo, nome_original, mimetype, dados)
           VALUES (?, ?, ?, ?, ?)""",
        (pedido_id, tipo, arquivo_info["nome"], arquivo_info["mimetype"], to_blob(arquivo_info["dados"])),
    )


def _acesso_projeto(conn, pedido_id):
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        return None, None
    if session.get("admin_logado"):
        return pedido, "admin"
    if session.get("cliente_id") and pedido["cliente_id"] == session.get("cliente_id"):
        return pedido, "cliente"
    if session.get("impressora_id") and pedido["impressora_id"] == session.get("impressora_id"):
        return pedido, "impressora"
    return pedido, None


def _mensagem_sistema(conn, pedido_id, texto):
    conn.execute(
        """INSERT INTO pedido_mensagens
           (pedido_id, autor_tipo, autor_id, texto, lida_cliente, lida_impressora)
           VALUES (?, 'sistema', NULL, ?, 0, 0)""",
        (pedido_id, texto),
    )


@app.route("/orcamento", methods=["GET", "POST"])
def orcamento():
    resultado = None
    form = {
        "categoria": "tecnica", "altura": 10, "largura": 10, "profundidade": 10,
        "quantidade": 1, "material": "pla", "qualidade": "padrao", "complexidade": "media",
    }

    # A mesma regra de preço é usada no servidor e no preview do navegador.
    conn_cfg = get_db()
    config_precos = get_configs(conn_cfg)
    conn_cfg.close()
    regras = regras_precificacao(config_precos)
    materiais_front = materiais_com_preco(regras)
    regra_front = {
        "shell_fraction": SHELL_FRACTION,
        "cat_acabamento": CAT_ACABAMENTO,
        "cat_preparacao": CAT_PREPARACAO,
        "hora_fdm": regras["hora_fdm"],
        "hora_resina": regras["hora_resina"],
        "reserva_falha_pct": regras["reserva_falha_pct"],
        "margem_impressor_pct": regras["margem_impressor_pct"],
        "comissao_voxxel_pct": regras["comissao_voxxel_pct"],
        "pedido_minimo": regras["pedido_minimo"],
    }

    def contexto_orcamento(resultado_local=None):
        return dict(
            form=form, resultado=resultado_local,
            materiais=materiais_front, qualidades=QUALIDADE, complexidades=COMPLEXIDADE,
            materiais_js=materiais_front, qualidade_js=QUALIDADE,
            complexidade_js=COMPLEXIDADE, cliente_logado=cliente_logado,
            regra_js=regra_front,
        )

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
            return render_template("orcamento.html", **contexto_orcamento(None))

        if not all(math.isfinite(form[chave]) for chave in ("altura", "largura", "profundidade")):
            flash("As dimensões informadas não são válidas.")
            return render_template("orcamento.html", **contexto_orcamento(None))
        if (form["categoria"] not in CAT_ACABAMENTO or form["material"] not in MATERIAIS or
                form["qualidade"] not in QUALIDADE or form["complexidade"] not in COMPLEXIDADE):
            flash("Uma das opções selecionadas não é válida. Revise a configuração do projeto.")
            return render_template("orcamento.html", **contexto_orcamento(None))
        if any(form[chave] <= 0 or form[chave] > 300 for chave in ("altura", "largura", "profundidade")):
            flash("Informe dimensões maiores que zero e de até 300 cm por eixo para usar a simulação automática.")
            return render_template("orcamento.html", **contexto_orcamento(None))
        if form["quantidade"] > 100:
            flash("Para mais de 100 unidades, envie o projeto em lotes ou fale com a Voxxel para uma cotação específica.")
            return render_template("orcamento.html", **contexto_orcamento(None))

        resultado = calcular_orcamento(
            form["altura"], form["largura"], form["profundidade"], form["quantidade"],
            form["categoria"], form["complexidade"], form["material"], form["qualidade"],
            regras=regras,
        )
        resultado["tempo_formatado"] = formatar_horas(resultado["horas_total"])

        if request.form.get("acao") == "enviar":
            if not session.get("cliente_id"):
                flash("Faça login para enviar o projeto e acompanhá-lo em ‘Minha conta’. Depois de entrar, volte a esta página e reanexe os arquivos de referência.")
                return redirect(url_for("conta_entrar", next=url_for("orcamento")))

            nome = texto_seguro(request.form.get("nome"), 120)
            telefone = normalizar_telefone(request.form.get("telefone"))
            if not nome or not 10 <= len(telefone) <= 13:
                flash("Preencha seu nome e um telefone válido com DDD para enviar o projeto.")
                return render_template("orcamento.html", **contexto_orcamento(resultado))

            descricao_projeto = texto_seguro(request.form.get("descricao_projeto"), 1400)
            requisitos_projeto = texto_seguro(request.form.get("requisitos_projeto"), 1400)
            uso_projeto = texto_seguro(request.form.get("uso_projeto"), 1000)
            alteracoes_projeto = texto_seguro(request.form.get("alteracoes_projeto"), 1000)
            if len(descricao_projeto) < 12 or len(requisitos_projeto) < 8 or len(uso_projeto) < 8:
                flash("Descreva o que você precisa, o que deve ser respeitado e como a peça será usada.")
                return render_template("orcamento.html", **contexto_orcamento(resultado))

            try:
                modelo = validar_modelo_3d(request.files.get("modelo_3d"))
                imagens = []
                for arq in request.files.getlist("imagens_referencia")[:6]:
                    info = validar_imagem_referencia(arq)
                    if info:
                        imagens.append(info)
            except ValueError as erro:
                flash(str(erro))
                return render_template("orcamento.html", **contexto_orcamento(resultado))

            if not modelo and not imagens:
                flash("Envie um arquivo 3D ou pelo menos uma imagem de referência para conseguirmos entender o projeto.")
                return render_template("orcamento.html", **contexto_orcamento(resultado))

            referencia_status = "arquivo_3d" if modelo else "imagem_descricao"
            detalhes = (
                f"Categoria: {resultado['categoria_nome']}\n"
                f"Dimensões: {form['altura']}x{form['largura']}x{form['profundidade']} cm\n"
                f"Material: {resultado['material_nome']}\n"
                f"Qualidade: {form['qualidade']}\n"
                f"Complexidade: {form['complexidade']}\n"
                f"Quantidade: {form['quantidade']}\n"
                f"Projeto: {descricao_projeto}"
            )
            cliente_lat = ler_coordenada_formulario(request.form.get("cliente_lat"), -90, 90)
            cliente_lng = ler_coordenada_formulario(request.form.get("cliente_lng"), -180, 180)
            conn = get_db()
            pedido_id = criar_pedido(
                conn, "orcamento", detalhes, resultado["preco_total"], nome, telefone,
                ("pix" if pix.chave_valida(config_precos.get("pix_chave", "")) else
                 "cartao" if config_precos.get("mp_access_token", "").strip() else "combinar"),
                cliente_id=session["cliente_id"], cliente_lat=cliente_lat, cliente_lng=cliente_lng,
                material_requisito=form["material"],
            )
            conn.execute(
                """UPDATE pedidos SET descricao_projeto=?, requisitos_projeto=?, uso_projeto=?,
                   alteracoes_projeto=?, referencia_status=?, fluxo_status='recebido' WHERE id=?""",
                (descricao_projeto, requisitos_projeto, uso_projeto, alteracoes_projeto, referencia_status, pedido_id),
            )
            if modelo:
                inserir_referencia(conn, pedido_id, "modelo_3d", modelo)
            for imagem in imagens:
                inserir_referencia(conn, pedido_id, "imagem", imagem)
            _mensagem_sistema(conn, pedido_id, "Projeto enviado para a Rede Voxxel. O parceiro responsável poderá solicitar detalhes antes da produção.")
            conn.commit()
            distribuicao.despachar_pedido(conn, pedido_id)
            conn.close()
            flash("Projeto recebido! Agora ele pode ser analisado por um parceiro da Rede Voxxel.")
            return redirect(url_for("pedido_projeto", pedido_id=pedido_id))

    return render_template("orcamento.html", **contexto_orcamento(resultado))


@app.route("/api/projetos/mensagem-pendente")
def api_projeto_mensagem_pendente():
    conn = get_db()
    row = None
    papel = None
    if session.get("cliente_id"):
        papel = "cliente"
        row = conn.execute(
            """SELECT m.id, m.pedido_id, m.texto, m.anexo_nome, m.autor_tipo
               FROM pedido_mensagens m JOIN pedidos p ON p.id=m.pedido_id
               WHERE p.cliente_id=? AND m.lida_cliente=0 AND m.autor_tipo!='cliente'
               ORDER BY m.id DESC LIMIT 1""", (session["cliente_id"],)
        ).fetchone()
    elif session.get("impressora_id"):
        papel = "impressora"
        row = conn.execute(
            """SELECT m.id, m.pedido_id, m.texto, m.anexo_nome, m.autor_tipo
               FROM pedido_mensagens m JOIN pedidos p ON p.id=m.pedido_id
               WHERE p.impressora_id=? AND m.lida_impressora=0 AND m.autor_tipo!='impressora'
               ORDER BY m.id DESC LIMIT 1""", (session["impressora_id"],)
        ).fetchone()
    if not row:
        conn.close(); return {"ok": True, "mensagem": None}
    texto = (row["texto"] or "").strip()
    if not texto:
        texto = "Novo arquivo anexado ao projeto." if row["anexo_nome"] else "Há uma atualização no projeto."
    dados = {
        "id": row["id"], "pedido_id": row["pedido_id"], "texto": texto[:180],
        "autor": "Voxxel" if row["autor_tipo"] in ("sistema", "admin") else ("Cliente" if row["autor_tipo"] == "cliente" else "Parceiro de produção"),
        "url": url_for("pedido_projeto", pedido_id=row["pedido_id"]), "papel": papel,
    }
    conn.close(); return {"ok": True, "mensagem": dados}


@app.route("/pedido/<int:pedido_id>/projeto")
def pedido_projeto(pedido_id):
    conn = get_db()
    pedido, papel = _acesso_projeto(conn, pedido_id)
    if not pedido:
        conn.close()
        abort(404)
    if not papel:
        conn.close()
        if not (session.get("cliente_id") or session.get("impressora_id") or session.get("admin_logado")):
            flash("Entre na sua conta para acessar a conversa deste projeto.")
            return redirect(url_for("conta_entrar"))
        abort(403)

    if papel == "cliente":
        conn.execute("UPDATE pedido_mensagens SET lida_cliente=1 WHERE pedido_id=?", (pedido_id,))
    elif papel == "impressora":
        conn.execute("UPDATE pedido_mensagens SET lida_impressora=1 WHERE pedido_id=?", (pedido_id,))
    conn.commit()

    referencias = conn.execute("SELECT id, tipo, nome_original, mimetype, criado_em FROM pedido_referencias WHERE pedido_id=? ORDER BY id", (pedido_id,)).fetchall()
    mensagens = conn.execute("SELECT id, autor_tipo, autor_id, texto, anexo_nome, anexo_mimetype, criado_em FROM pedido_mensagens WHERE pedido_id=? ORDER BY id", (pedido_id,)).fetchall()
    impressora = buscar_impressora_por_id(conn, pedido["impressora_id"]) if pedido["impressora_id"] else None
    conn.close()
    return render_template("pedido_projeto.html", pedido=pedido, papel=papel, referencias=referencias,
                           mensagens=mensagens, impressora=impressora, fluxo_labels=FLUXO_LABELS)


@app.route("/pedido/<int:pedido_id>/projeto/api/estado")
def pedido_projeto_api_estado(pedido_id):
    conn = get_db()
    pedido, papel = _acesso_projeto(conn, pedido_id)
    if not papel:
        conn.close(); return {"ok": False}, 403
    ultima = conn.execute("SELECT COALESCE(MAX(id),0) AS id FROM pedido_mensagens WHERE pedido_id=?", (pedido_id,)).fetchone()["id"]
    status = pedido["fluxo_status"] or "recebido"
    conn.close()
    return {"ok": True, "ultima_mensagem_id": ultima, "status": status, "label": FLUXO_LABELS.get(status, status)}


@app.route("/pedido/<int:pedido_id>/referencia/<int:referencia_id>")
def pedido_referencia(pedido_id, referencia_id):
    conn = get_db()
    _pedido, papel = _acesso_projeto(conn, pedido_id)
    if not papel:
        conn.close(); abort(403)
    ref = conn.execute("SELECT * FROM pedido_referencias WHERE id=? AND pedido_id=?", (referencia_id, pedido_id)).fetchone()
    if not ref:
        conn.close(); abort(404)
    dados = bytes(ref["dados"])
    nome = ref["nome_original"]
    mimetype = ref["mimetype"] or "application/octet-stream"
    conn.close()
    return send_file(io.BytesIO(dados), mimetype=mimetype, download_name=nome, as_attachment=not mimetype.startswith("image/"))


@app.route("/pedido/<int:pedido_id>/projeto/mensagem", methods=["POST"])
def pedido_projeto_mensagem(pedido_id):
    conn = get_db()
    pedido, papel = _acesso_projeto(conn, pedido_id)
    if papel not in ("cliente", "impressora", "admin"):
        conn.close(); abort(403)
    texto = texto_seguro(request.form.get("mensagem"), 2500)
    anexo = request.files.get("anexo")
    anexo_info = None
    if anexo and anexo.filename:
        try:
            if _extensao(anexo.filename) in EXTENSOES_MODELO_PERMITIDAS:
                anexo_info = validar_modelo_3d(anexo, MAX_ANEXO_CHAT_BYTES)
            else:
                anexo_info = validar_imagem_referencia(anexo, MAX_ANEXO_CHAT_BYTES)
        except ValueError as erro:
            conn.close(); flash(str(erro)); return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
    if not texto and not anexo_info:
        conn.close(); flash("Escreva uma mensagem ou anexe uma referência."); return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
    autor_id = session.get("cliente_id") if papel == "cliente" else session.get("impressora_id") if papel == "impressora" else None
    conn.execute(
        """INSERT INTO pedido_mensagens
           (pedido_id, autor_tipo, autor_id, texto, anexo_nome, anexo_mimetype, anexo_dados, lida_cliente, lida_impressora)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pedido_id, papel, autor_id, texto,
         anexo_info["nome"] if anexo_info else None,
         anexo_info["mimetype"] if anexo_info else None,
         to_blob(anexo_info["dados"]) if anexo_info else None,
         1 if papel == "cliente" else 0,
         1 if papel == "impressora" else 0),
    )
    if papel == "cliente" and pedido["fluxo_status"] in ("precisa_info", "aguardando_aprovacao"):
        conn.execute("UPDATE pedidos SET fluxo_status='cliente_respondeu', aprovado_cliente=0, producao_autorizada=0 WHERE id=?", (pedido_id,))
    elif papel == "impressora" and request.form.get("solicitar_info") == "1":
        conn.execute("UPDATE pedidos SET fluxo_status='precisa_info', aprovado_cliente=0, producao_autorizada=0 WHERE id=?", (pedido_id,))
    conn.commit(); conn.close()
    return redirect(url_for("pedido_projeto", pedido_id=pedido_id))


@app.route("/pedido/<int:pedido_id>/mensagem/<int:mensagem_id>/anexo")
def pedido_mensagem_anexo(pedido_id, mensagem_id):
    conn = get_db()
    _pedido, papel = _acesso_projeto(conn, pedido_id)
    if not papel:
        conn.close(); abort(403)
    msg = conn.execute("SELECT * FROM pedido_mensagens WHERE id=? AND pedido_id=?", (mensagem_id, pedido_id)).fetchone()
    if not msg or not msg["anexo_dados"]:
        conn.close(); abort(404)
    dados = bytes(msg["anexo_dados"]); nome = msg["anexo_nome"] or "anexo"; mimetype = msg["anexo_mimetype"] or "application/octet-stream"
    conn.close()
    return send_file(io.BytesIO(dados), mimetype=mimetype, download_name=nome, as_attachment=not mimetype.startswith("image/"))


@app.route("/pedido/<int:pedido_id>/projeto/status", methods=["POST"])
def pedido_projeto_status(pedido_id):
    conn = get_db()
    pedido, papel = _acesso_projeto(conn, pedido_id)
    if papel not in ("impressora", "admin"):
        conn.close(); abort(403)
    acao = request.form.get("acao")
    atual = pedido["fluxo_status"] or "recebido"
    if acao == "solicitar_info" and atual in ("em_analise", "cliente_respondeu", "aguardando_aprovacao", "recebido"):
        novo, texto = "precisa_info", "O parceiro precisa de mais informações para compreender o projeto."
    elif acao == "enviar_aprovacao" and atual in ("em_analise", "cliente_respondeu", "recebido"):
        if pedido["tipo"] == "orcamento" and pedido["status_pagamento"] != "confirmado":
            bruto = (request.form.get("valor_final") or "").strip().replace(",", ".")
            try:
                valor_final = round(float(bruto), 2)
            except (TypeError, ValueError):
                conn.close(); flash("Informe um valor final válido para enviar à aprovação."); return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
            if not 1 <= valor_final <= 100000:
                conn.close(); flash("O valor final precisa ficar entre R$ 1,00 e R$ 100.000,00."); return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
            conn.execute("UPDATE pedidos SET valor_estimado=? WHERE id=?", (valor_final, pedido_id))
            if pedido["impressora_id"]:
                aplicar_comissao_pedido(conn, pedido_id, valor_final)
        novo, texto = "aguardando_aprovacao", "O parceiro concluiu a análise. Revise as referências, o valor final e confirme se o projeto está correto."
    elif acao == "iniciar_producao" and atual == "producao_autorizada" and pedido["producao_autorizada"] and pedido["status_pagamento"] == "confirmado":
        novo, texto = "em_producao", "A produção foi iniciada pelo parceiro responsável."
        conn.execute("UPDATE pedidos SET status='andamento' WHERE id=?", (pedido_id,))
    elif acao == "marcar_pronto" and pedido["fluxo_status"] == "em_producao":
        novo, texto = "pronto", "O parceiro marcou o pedido como pronto."
    elif acao == "concluir" and pedido["fluxo_status"] == "pronto":
        novo, texto = "concluido", "Pedido concluído."
        conn.execute("UPDATE pedidos SET status='concluido' WHERE id=?", (pedido_id,))
    else:
        conn.close(); flash("Essa mudança de etapa não está disponível agora."); return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
    conn.execute("UPDATE pedidos SET fluxo_status=? WHERE id=?", (novo, pedido_id))
    _mensagem_sistema(conn, pedido_id, texto)
    conn.commit(); conn.close()
    return redirect(url_for("pedido_projeto", pedido_id=pedido_id))


@app.route("/pedido/<int:pedido_id>/projeto/aprovar", methods=["POST"])
@login_cliente_obrigatorio
def pedido_projeto_aprovar(pedido_id):
    conn = get_db()
    pedido, papel = _acesso_projeto(conn, pedido_id)
    if papel != "cliente":
        conn.close(); abort(403)
    if pedido["fluxo_status"] != "aguardando_aprovacao":
        conn.close(); flash("Este projeto ainda não está aguardando aprovação."); return redirect(url_for("pedido_projeto", pedido_id=pedido_id))
    conn.execute("UPDATE pedidos SET aprovado_cliente=1, producao_autorizada=1, fluxo_status='producao_autorizada' WHERE id=?", (pedido_id,))
    _mensagem_sistema(conn, pedido_id, "O cliente aprovou o projeto e autorizou o avanço para produção.")
    conn.commit(); conn.close()
    flash("Projeto aprovado. O parceiro poderá avançar quando o pagamento estiver confirmado.")
    return redirect(url_for("pedido_projeto", pedido_id=pedido_id))


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
        aceitou_termos = request.form.get("aceite_termos") == "1"

        erro = None
        if not nome:
            erro = "Preencha seu nome."
        elif not TELEFONE_MIN_DIGITOS <= len(telefone) <= 13:
            erro = "Informe um telefone válido, com DDD."
        elif not 8 <= len(senha) <= 128:
            erro = "A senha precisa ter entre 8 e 128 caracteres."
        elif senha != confirmar_senha:
            erro = "As senhas não coincidem."
        elif not aceitou_termos:
            erro = "Confirme que você leu os Termos de Uso e a Política de Privacidade."

        conn = get_db()
        if not erro and buscar_cliente_por_telefone(conn, telefone):
            erro = "Já existe uma conta com esse telefone. Faça login."

        if erro:
            conn.close()
            flash(erro)
            return render_template("conta_cadastro.html")

        try:
            cliente_id = criar_cliente(conn, nome, telefone, generate_password_hash(senha))
            conn.execute("UPDATE clientes SET termos_aceitos_em=CURRENT_TIMESTAMP WHERE id=?", (cliente_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            flash("Não foi possível criar a conta com esse telefone. Se ele já estiver cadastrado, faça login.")
            return render_template("conta_cadastro.html")
        conn.close()

        carrinho_salvo = dict(carrinho_sessao())
        session.clear()
        session["carrinho"] = carrinho_salvo
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
                limpar_falhas_login(chave_rate_limit)
                carrinho_salvo = dict(carrinho_sessao())
                session.clear()
                session["carrinho"] = carrinho_salvo
                session["cliente_id"] = cliente["id"]
                session["cliente_nome"] = cliente["nome"]
                session.permanent = True
                return redirect(next_seguro(url_for("conta_dashboard")))
            registrar_falha_login(chave_rate_limit)
            erro = "Não foi possível entrar. Confira o telefone com DDD e a senha deste cadastro."

    return render_template("conta_entrar.html", erro=erro)


@app.route("/conta/sair", methods=["POST"])
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
    return render_template("conta_dashboard.html", pedidos=pedidos, fluxo_labels=FLUXO_LABELS)


@app.route("/conta/perfil", methods=["GET", "POST"])
@login_cliente_obrigatorio
def conta_perfil():
    conn = get_db()
    cliente = buscar_cliente_por_id(conn, session["cliente_id"])
    if not cliente:
        conn.close(); session.clear(); return redirect(url_for("conta_entrar"))
    if request.method == "POST":
        acao = request.form.get("acao", "perfil")
        if acao == "perfil":
            nome = texto_seguro(request.form.get("nome"), 120)
            telefone = normalizar_telefone(request.form.get("telefone"))
            existente = buscar_cliente_por_telefone(conn, telefone) if telefone else None
            if not nome or not TELEFONE_MIN_DIGITOS <= len(telefone) <= 13:
                flash("Informe seu nome e um telefone válido com DDD.")
            elif existente and existente["id"] != cliente["id"]:
                flash("Este telefone já está vinculado a outra conta.")
            else:
                conn.execute("UPDATE clientes SET nome=?,telefone=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=?", (nome, telefone, cliente["id"]))
                conn.commit(); session["cliente_nome"] = nome; flash("Dados da conta atualizados.")
        elif acao == "senha":
            atual = request.form.get("senha_atual", "")
            nova = request.form.get("nova_senha", "")
            confirmar = request.form.get("confirmar_nova_senha", "")
            if not check_password_hash(cliente["senha_hash"], atual):
                flash("A senha atual não confere.")
            elif not 8 <= len(nova) <= 128:
                flash("A nova senha precisa ter entre 8 e 128 caracteres.")
            elif nova != confirmar:
                flash("A confirmação da nova senha não coincide.")
            else:
                conn.execute("UPDATE clientes SET senha_hash=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=?", (generate_password_hash(nova), cliente["id"]))
                conn.commit(); flash("Senha alterada com segurança.")
        cliente = buscar_cliente_por_id(conn, session["cliente_id"])
    conn.close()
    return render_template("conta_perfil.html", cliente=cliente)


# ---------- admin ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    erro = None
    ip = request.remote_addr or "desconhecido"
    chave_rate_limit = f"admin:{ip}"
    if request.method == "POST":
        if login_bloqueado(chave_rate_limit):
            erro = "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."
        elif not ADMIN_PASSWORD:
            erro = "O acesso administrativo ainda não foi configurado no servidor."
        elif secrets.compare_digest(request.form.get("senha", ""), ADMIN_PASSWORD):
            limpar_falhas_login(chave_rate_limit)
            session.clear()
            session["admin_logado"] = True
            session.permanent = True  # expira sozinho após PERMANENT_SESSION_LIFETIME
            return redirect(url_for("admin_dashboard"))
        else:
            registrar_falha_login(chave_rate_limit)
            erro = "Senha incorreta."
    return render_template("admin_login.html", erro=erro)


@app.route("/admin/logout", methods=["POST"])
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

    receita_confirmada = sum(p["valor_estimado"] for p in pedidos if p["status_pagamento"] == "confirmado")
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
        pix_configurado=pix.chave_valida(config.get("pix_chave", "")),
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
            if not math.isfinite(comissao_pct):
                raise ValueError
        except (ValueError, AttributeError):
            comissao_pct = 15.0
        comissao_pct = max(0.0, min(comissao_pct, 80.0))  # alinhado ao limite da calculadora
        def _numero_config(nome, padrao, minimo=0.0, maximo=None):
            try:
                valor = float(request.form.get(nome, str(padrao)).replace(",", "."))
                if not math.isfinite(valor):
                    raise ValueError
            except (ValueError, AttributeError):
                valor = float(padrao)
            valor = max(minimo, valor)
            if maximo is not None:
                valor = min(maximo, valor)
            return str(valor)

        config_atual = get_configs(conn)
        token_novo = request.form.get("mp_access_token", "").strip()
        pix_chave_nova = texto_seguro(request.form.get("pix_chave"), 180)
        if pix_chave_nova and not pix.chave_valida(pix_chave_nova):
            flash("A chave Pix informada é inválida ou longa demais para gerar um QR Code compatível.")
            config = dict(config_atual)
            conn.close()
            return render_template("admin_configuracoes.html", config=config), 400
        set_configs(conn, {
            "vendedor_nome": texto_seguro(request.form.get("vendedor_nome"), 120) or "Voxxel",
            "pix_chave": pix_chave_nova,
            "pix_nome": texto_seguro(request.form.get("pix_nome"), 80) or "Voxxel Impressao 3D",
            "pix_cidade": texto_seguro(request.form.get("pix_cidade"), 80) or "Sao Jose dos Pinhais",
            "whatsapp": normalizar_telefone(request.form.get("whatsapp"))[:15],
            "mp_access_token": token_novo if token_novo else config_atual.get("mp_access_token", ""),
            "comissao_percentual": str(comissao_pct),
            "custo_kg_pla": _numero_config("custo_kg_pla", 95),
            "custo_kg_petg": _numero_config("custo_kg_petg", 110),
            "custo_kg_abs": _numero_config("custo_kg_abs", 105),
            "custo_kg_resina": _numero_config("custo_kg_resina", 150),
            "preco_hora_fdm": _numero_config("preco_hora_fdm", 4.50),
            "preco_hora_resina": _numero_config("preco_hora_resina", 7.00),
            "reserva_falha_percentual": _numero_config("reserva_falha_percentual", 10, 0, 60),
            "margem_impressor_percentual": _numero_config("margem_impressor_percentual", 30, 0, 200),
            "pedido_minimo": _numero_config("pedido_minimo", 18.90),
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


def _imagem_webp_segura(dados, max_pixels=25_000_000, max_dim=1800, qualidade=86):
    """Valida, redimensiona e reencoda uma imagem sem EXIF/metadados.

    Além de reduzir peso, evita persistir metadados de câmera/localização de
    arquivos enviados por clientes, parceiros ou administradores.
    """
    try:
        imagem = Image.open(io.BytesIO(dados))
        if imagem.width <= 0 or imagem.height <= 0 or imagem.width * imagem.height > max_pixels:
            raise ValueError
        imagem.load()
    except Exception as exc:
        raise ValueError("O arquivo enviado não é uma imagem válida.") from exc
    if max(imagem.size) > max_dim:
        imagem.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    if imagem.mode not in ("RGB", "RGBA"):
        imagem = imagem.convert("RGBA" if "A" in imagem.getbands() else "RGB")
    saida = io.BytesIO()
    imagem.save(saida, format="WEBP", quality=qualidade, method=4)
    return saida.getvalue(), "image/webp"


def processar_upload_imagem(arquivo):
    """Valida e normaliza imagem de produto para WEBP otimizado."""
    if not arquivo or not arquivo.filename:
        return None
    dados = arquivo.read(8 * 1024 * 1024 + 1)
    if not dados:
        return None
    if len(dados) > 8 * 1024 * 1024:
        flash("A imagem do produto pode ter no máximo 8 MB.")
        return None
    try:
        return _imagem_webp_segura(dados)
    except ValueError as erro:
        flash(str(erro))
        return None



def normalizar_angulo_imagem(valor):
    bruto = str(valor or "0deg").strip().lower()
    if bruto.endswith("deg"):
        bruto = bruto[:-3]
    try:
        grau = float(bruto) % 360
    except (TypeError, ValueError):
        grau = 0
    return f"{grau:.0f}deg"


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
        valor = float(request.form.get("preco", "0").replace(",", "."))
        if not math.isfinite(valor) or valor < 0.01:
            return None
        return round(valor, 2)
    except (ValueError, TypeError, AttributeError):
        return None


@app.route("/admin/produtos/novo", methods=["GET", "POST"])
@login_obrigatorio
def admin_produto_novo():
    if request.method == "POST":
        preco = ler_preco_formulario()
        categoria = request.form.get("categoria", "")
        material = request.form.get("material", "pla")
        if (preco is None or not request.form.get("nome", "").strip() or
                categoria not in CATEGORIAS or material not in MATERIAIS):
            flash("Preencha nome, categoria, material e preço corretamente.")
            return render_template("admin_produto_form.html", produto=None, materiais=MATERIAIS)

        conn = get_db()
        resultado_imagem = processar_upload_imagem(request.files.get("imagem"))
        imagem_dados, imagem_mimetype = resultado_imagem if resultado_imagem else (None, None)
        conn.execute(
            """INSERT INTO produtos
               (nome, categoria, preco, descricao, imagem_ang, ativo, imagem_dados, imagem_mimetype, estoque, material)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                texto_seguro(request.form["nome"], 120), categoria, preco,
                texto_seguro(request.form.get("descricao"), 2000), normalizar_angulo_imagem(request.form.get("imagem_ang")),
                1 if request.form.get("ativo") else 0,
                to_blob(imagem_dados), imagem_mimetype, ler_estoque_formulario(), material,
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_produtos"))
    return render_template("admin_produto_form.html", produto=None, materiais=MATERIAIS)


@app.route("/admin/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@login_obrigatorio
def admin_produto_editar(produto_id):
    conn = get_db()
    if request.method == "POST":
        preco = ler_preco_formulario()
        categoria = request.form.get("categoria", "")
        material = request.form.get("material", "pla")
        if (preco is None or not request.form.get("nome", "").strip() or
                categoria not in CATEGORIAS or material not in MATERIAIS):
            flash("Preencha nome, categoria, material e preço corretamente.")
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
                   imagem_dados=?, imagem_mimetype=?, estoque=?, material=? WHERE id=?""",
                (
                    nome, categoria, preco,
                    descricao, normalizar_angulo_imagem(request.form.get("imagem_ang")),
                    1 if request.form.get("ativo") else 0,
                    to_blob(imagem_dados), imagem_mimetype, estoque, material, produto_id,
                ),
            )
        elif request.form.get("remover_imagem"):
            # Usuário marcou pra remover a imagem atual, sem enviar outra
            conn.execute(
                """UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?,
                   imagem_dados=NULL, imagem_mimetype=NULL, estoque=?, material=? WHERE id=?""",
                (
                    nome, categoria, preco,
                    descricao, normalizar_angulo_imagem(request.form.get("imagem_ang")),
                    1 if request.form.get("ativo") else 0, estoque, material, produto_id,
                ),
            )
        else:
            # Mantém a imagem que já existia
            conn.execute(
                "UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?, estoque=?, material=? WHERE id=?",
                (
                    nome, categoria, preco,
                    descricao, normalizar_angulo_imagem(request.form.get("imagem_ang")),
                    1 if request.form.get("ativo") else 0, estoque, material, produto_id,
                ),
            )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_produtos"))
    produto = conn.execute(
        f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos WHERE id = ?", (produto_id,)
    ).fetchone()
    conn.close()
    return render_template("admin_produto_form.html", produto=produto, materiais=MATERIAIS)


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

    contagens = {"todos": len(todos), "novo": 0, "andamento": 0, "concluido": 0, "cancelado": 0}
    for p in todos:
        contagens[p["status"]] = contagens.get(p["status"], 0) + 1

    if status_filtro in ("novo", "andamento", "concluido", "cancelado"):
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
    pedido = conn.execute(
        "SELECT id, impressora_id, fluxo_status, status, material_requisito FROM pedidos WHERE id = ?",
        (pedido_id,),
    ).fetchone()
    impressora = buscar_impressora_por_id(conn, impressora_id)
    if not pedido:
        conn.close()
        flash("Pedido não encontrado.")
        return redirect(url_for("admin_pedidos"))
    if not impressora or not impressora["ativo"]:
        conn.close()
        flash("Esse parceiro não está disponível para receber pedidos.")
        return redirect(url_for("admin_pedidos"))
    if pedido["status"] in ("cancelado", "concluido") or pedido["fluxo_status"] in ("cancelado", "concluido"):
        conn.close()
        flash("Pedidos cancelados ou concluídos não podem ser atribuídos a outro parceiro.")
        return redirect(url_for("admin_pedidos"))
    requeridos = {m.strip().lower() for m in (pedido["material_requisito"] or "").split(",") if m.strip()}
    suportados = {m.strip().lower() for m in (impressora["materiais"] or "").split(",") if m.strip()}
    if requeridos and not requeridos.issubset(suportados):
        conn.close()
        flash("Esse parceiro não suporta todos os materiais exigidos pelo pedido.")
        return redirect(url_for("admin_pedidos"))
    if pedido["impressora_id"] and pedido["fluxo_status"] in ("em_producao", "pronto", "concluido"):
        conn.close()
        flash("Não é seguro trocar a impressora depois que a produção já começou.")
        return redirect(url_for("admin_pedidos"))

    if not distribuicao.atribuir_manualmente(conn, pedido_id, impressora_id):
        conn.close()
        flash("O pedido mudou de estado antes da atribuição. Atualize a página e tente novamente.")
        return redirect(url_for("admin_pedidos"))
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
    if novo_status not in ("novo", "andamento", "concluido", "cancelado"):
        flash("Status inválido.")
        return redirect(url_for("admin_pedidos"))
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close(); abort(404)
    if pedido["status"] == "cancelado" and novo_status != "cancelado":
        conn.close()
        flash("Um pedido cancelado não pode ser reaberto automaticamente porque o estoque já pode ter sido devolvido. Crie uma nova tratativa/pedido.")
        return redirect(url_for("admin_pedidos"))
    if pedido["status"] == "concluido" and novo_status != "concluido":
        conn.close()
        flash("Um pedido concluído não pode voltar para uma etapa operacional pelo seletor de status.")
        return redirect(url_for("admin_pedidos"))
    if novo_status == "cancelado":
        if pedido["fluxo_status"] in ("em_producao", "pronto", "concluido"):
            conn.close()
            flash("Não cancele automaticamente um pedido depois que a produção começou. Faça a tratativa manual.")
            return redirect(url_for("admin_pedidos"))
        if not pedido["estoque_devolvido"] and pedido["tipo"] == "loja":
            itens = conn.execute("SELECT produto_id, quantidade FROM pedido_itens WHERE pedido_id = ?", (pedido_id,)).fetchall()
            for item in itens:
                if item["produto_id"]:
                    conn.execute(
                        "UPDATE produtos SET estoque = estoque + ? WHERE id = ? AND estoque IS NOT NULL",
                        (item["quantidade"], item["produto_id"]),
                    )
            conn.execute("UPDATE pedidos SET estoque_devolvido=1 WHERE id=?", (pedido_id,))
        conn.execute(
            "UPDATE pedidos SET status='cancelado', fluxo_status='cancelado', producao_autorizada=0, cancelado_em=CURRENT_TIMESTAMP WHERE id=?",
            (pedido_id,),
        )
        conn.execute(
            "UPDATE ofertas_impressao SET status='expirada', respondido_em=CURRENT_TIMESTAMP WHERE pedido_id=? AND status='pendente'",
            (pedido_id,),
        )
        _mensagem_sistema(conn, pedido_id, "Pedido cancelado pela Voxxel.")
    elif novo_status == "concluido":
        conn.execute("UPDATE pedidos SET status='concluido', fluxo_status='concluido' WHERE id=?", (pedido_id,))
    else:
        conn.execute("UPDATE pedidos SET status = ? WHERE id = ?", (novo_status, pedido_id))
    conn.commit()
    pagamento_ja_confirmado = pedido["status_pagamento"] == "confirmado"
    conn.close()
    if novo_status == "cancelado":
        flash("Pedido cancelado e estoque elegível devolvido." + (" O pagamento estava confirmado: faça a tratativa de estorno/reembolso separadamente." if pagamento_ja_confirmado else ""))
    elif novo_status == "concluido":
        flash("Pedido marcado como concluído.")
    else:
        flash("Status do pedido atualizado.")
    return redirect(url_for("admin_pedidos"))


@app.route("/admin/pedidos/<int:pedido_id>/pagamento", methods=["POST"])
@login_obrigatorio
def admin_pedido_pagamento(pedido_id):
    """Confirma ou reabre o pagamento sem confundir aviso do cliente com liquidação."""
    acao = request.form.get("acao", "")
    if acao not in ("confirmar", "reabrir"):
        abort(400)
    conn = get_db()
    pedido = conn.execute(
        "SELECT id, status, status_pagamento, fluxo_status FROM pedidos WHERE id = ?",
        (pedido_id,),
    ).fetchone()
    if not pedido:
        conn.close()
        abort(404)
    if pedido["status"] == "cancelado":
        conn.close()
        flash("O pagamento de um pedido cancelado deve ser tratado manualmente, sem alterar o estado do pedido.")
        return redirect(redirecionamento_seguro(url_for("admin_pedidos")))
    if acao == "reabrir" and pedido["fluxo_status"] in ("em_producao", "pronto", "concluido"):
        conn.close()
        flash("Não é seguro reabrir o pagamento depois que a produção já começou.")
        return redirect(redirecionamento_seguro(url_for("admin_pedidos")))
    novo = "confirmado" if acao == "confirmar" else "aguardando"
    conn.execute("UPDATE pedidos SET status_pagamento = ? WHERE id = ?", (novo, pedido_id))
    if acao == "confirmar":
        _mensagem_sistema(conn, pedido_id, "Pagamento confirmado pela Voxxel.")
    conn.commit()
    conn.close()
    flash("Pagamento confirmado." if acao == "confirmar" else "Pagamento reaberto para conferência.")
    return redirect(redirecionamento_seguro(url_for("admin_pedidos")))


@app.route("/admin/pedidos/<int:pedido_id>/repasse", methods=["POST"])
@login_obrigatorio
def admin_pedido_repasse(pedido_id):
    acao = request.form.get("acao", "")
    if acao not in ("liberar", "pagar", "reabrir"):
        abort(400)
    conn = get_db()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close(); abort(404)
    atual = pedido["repasse_status"] or "pendente"
    if not pedido["impressora_id"] or pedido["status_pagamento"] != "confirmado":
        conn.close(); flash("O repasse exige parceiro atribuído e pagamento confirmado."); return redirect(url_for("admin_pedidos"))
    if acao == "liberar":
        if pedido["status"] != "concluido" and pedido["fluxo_status"] != "concluido":
            conn.close(); flash("Conclua o pedido antes de liberar o repasse."); return redirect(url_for("admin_pedidos"))
        novo, pago_em, referencia = "liberado", None, ""
    elif acao == "pagar":
        if atual != "liberado":
            conn.close(); flash("Libere o repasse antes de marcá-lo como pago."); return redirect(url_for("admin_pedidos"))
        referencia = texto_seguro(request.form.get("referencia"), 180)
        if len(referencia) < 3:
            conn.close(); flash("Informe a referência ou identificação do comprovante."); return redirect(url_for("admin_pedidos"))
        novo, pago_em = "pago", "CURRENT_TIMESTAMP"
    else:
        if atual == "pago":
            conn.close(); flash("Um repasse já pago não pode ser reaberto automaticamente."); return redirect(url_for("admin_pedidos"))
        novo, pago_em, referencia = "pendente", None, ""
    if pago_em:
        conn.execute("UPDATE pedidos SET repasse_status=?,repasse_pago_em=CURRENT_TIMESTAMP,repasse_referencia=? WHERE id=?", (novo, referencia, pedido_id))
    else:
        conn.execute("UPDATE pedidos SET repasse_status=?,repasse_pago_em=NULL,repasse_referencia=? WHERE id=?", (novo, referencia, pedido_id))
    conn.commit(); conn.close()
    flash("Repasse liberado." if novo == "liberado" else ("Repasse marcado como pago." if novo == "pago" else "Repasse voltou para conferência."))
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
        materiais_selecionados = [m for m in request.form.getlist("materiais") if m in MATERIAIS]
        modelo_impressora = texto_seguro(request.form.get("modelo_impressora"), 160)
        tecnologia = request.form.get("tecnologia", "fdm")
        endereco_base = texto_seguro(request.form.get("endereco_base"), 300)
        pix_recebimento = texto_seguro(request.form.get("pix_recebimento"), 180)
        observacoes_equipamento = texto_seguro(request.form.get("observacoes_equipamento"), 500)
        aceitou_termos = request.form.get("aceite_termos") == "1"
        try:
            volume_x = float(request.form.get("volume_x", ""))
            volume_y = float(request.form.get("volume_y", ""))
            volume_z = float(request.form.get("volume_z", ""))
            capacidade_diaria_horas = float(request.form.get("capacidade_diaria_horas", ""))
        except (TypeError, ValueError):
            volume_x = volume_y = volume_z = capacidade_diaria_horas = 0

        erro = None
        if not nome:
            erro = "Preencha seu nome ou o nome do seu negócio."
        elif not TELEFONE_MIN_DIGITOS_IMPRESSORA <= len(telefone) <= 13:
            erro = "Informe um telefone válido, com DDD."
        elif not 8 <= len(senha) <= 128:
            erro = "A senha precisa ter entre 8 e 128 caracteres."
        elif senha != confirmar_senha:
            erro = "As senhas não coincidem."
        elif not materiais_selecionados:
            erro = "Selecione pelo menos um material que você consegue produzir."
        elif not modelo_impressora:
            erro = "Informe o modelo principal da sua impressora."
        elif tecnologia not in ("fdm", "resina", "ambas"):
            erro = "Selecione uma tecnologia de impressão válida."
        elif any(not math.isfinite(v) or v < 1 or v > 1000 for v in (volume_x, volume_y, volume_z)):
            erro = "Informe um volume de impressão válido, entre 1 e 1000 mm por eixo."
        elif not math.isfinite(capacidade_diaria_horas) or not 0.5 <= capacidade_diaria_horas <= 24:
            erro = "Informe uma capacidade diária entre 0,5 e 24 horas."
        elif len(endereco_base) < 8:
            erro = "Informe a cidade e o endereço base da operação."
        elif not pix_recebimento:
            erro = "Informe a chave Pix que será usada nos repasses."
        elif not aceitou_termos:
            erro = "Confirme que você leu os Termos de Uso e a Política de Privacidade."

        conn = get_db()
        if not erro and buscar_impressora_por_telefone(conn, telefone):
            erro = "Já existe um parceiro cadastrado com esse telefone. Faça login."

        if erro:
            conn.close()
            flash(erro)
            return render_template("impressora_cadastro.html", materiais=MATERIAIS)

        try:
            impressora_id = criar_impressora(
                conn, nome, telefone, generate_password_hash(senha), ",".join(materiais_selecionados)
            )
            conn.execute(
                """UPDATE impressoras SET termos_aceitos_em=CURRENT_TIMESTAMP,modelo_impressora=?,tecnologia=?,
                   volume_x=?,volume_y=?,volume_z=?,capacidade_diaria_horas=?,endereco_base=?,pix_recebimento=?,
                   observacoes_equipamento=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=?""",
                (modelo_impressora, tecnologia, volume_x, volume_y, volume_z, capacidade_diaria_horas,
                 endereco_base, pix_recebimento, observacoes_equipamento, impressora_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            flash("Não foi possível concluir o cadastro com esse telefone. Se ele já estiver cadastrado, faça login.")
            return render_template("impressora_cadastro.html", materiais=MATERIAIS)
        conn.close()

        session.clear()
        session["impressora_id"] = impressora_id
        session["impressora_nome"] = nome
        session.permanent = True
        flash("Cadastro enviado. Revise seus materiais no painel enquanto a Voxxel analisa a ativação da sua conta.")
        return redirect(url_for("impressora_painel"))

    return render_template("impressora_cadastro.html", materiais=MATERIAIS)


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
                limpar_falhas_login(chave_rate_limit)
                session.clear()
                session["impressora_id"] = impressora["id"]
                session["impressora_nome"] = impressora["nome"]
                session.permanent = True
                return redirect(next_seguro(url_for("impressora_painel")))
            registrar_falha_login(chave_rate_limit)
            erro = "Não foi possível entrar. Confira o telefone com DDD e a senha deste cadastro."

    return render_template("impressora_entrar.html", erro=erro)


@app.route("/impressora/sair", methods=["POST"])
def impressora_sair():
    session.pop("impressora_id", None)
    session.pop("impressora_nome", None)
    return redirect(url_for("home"))


@app.route("/impressora/painel")
@login_impressora_obrigatorio
def impressora_painel():
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    if impressora and impressora["online"] and not distribuicao.localizacao_recente(impressora):
        definir_status_impressora(conn, impressora["id"], False)
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
    ganho_confirmado = sum(
        (p["valor_estimado"] - (p["comissao_voxxel"] or 0))
        for p in pedidos_atribuidos if p["status_pagamento"] == "confirmado" and p["status"] != "cancelado"
    )
    ganho_liberado = sum(
        (p["valor_estimado"] - (p["comissao_voxxel"] or 0))
        for p in pedidos_atribuidos if (p["repasse_status"] or "pendente") == "liberado" and p["status"] != "cancelado"
    )
    ganho_pago = sum(
        (p["valor_estimado"] - (p["comissao_voxxel"] or 0))
        for p in pedidos_atribuidos if (p["repasse_status"] or "pendente") == "pago" and p["status"] != "cancelado"
    )
    conn.close()

    return render_template(
        "impressora_painel.html", impressora=impressora, oferta=oferta, oferta_pedido=oferta_pedido,
        oferta_distancia_km=oferta_distancia_km, oferta_segundos_restantes=oferta_segundos_restantes,
        oferta_ganho_estimado=oferta_ganho_estimado, pedidos=pedidos_atribuidos,
        ganho_confirmado=round(ganho_confirmado, 2), ganho_liberado=round(ganho_liberado, 2),
        ganho_pago=round(ganho_pago, 2), pct_comissao=pct_comissao, materiais=MATERIAIS,
        fluxo_labels=FLUXO_LABELS,
    )


@app.route("/impressora/perfil", methods=["GET", "POST"])
@login_impressora_obrigatorio
def impressora_perfil():
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    if not impressora:
        conn.close(); session.clear(); return redirect(url_for("impressora_entrar"))
    if request.method == "POST":
        acao = request.form.get("acao", "perfil")
        if acao == "perfil":
            nome = texto_seguro(request.form.get("nome"), 120)
            telefone = normalizar_telefone(request.form.get("telefone"))
            modelo = texto_seguro(request.form.get("modelo_impressora"), 160)
            tecnologia = request.form.get("tecnologia", "fdm")
            endereco = texto_seguro(request.form.get("endereco_base"), 300)
            pix_recebimento = texto_seguro(request.form.get("pix_recebimento"), 180)
            observacoes = texto_seguro(request.form.get("observacoes_equipamento"), 500)
            existente = buscar_impressora_por_telefone(conn, telefone) if telefone else None
            try:
                vx=float(request.form.get("volume_x", "")); vy=float(request.form.get("volume_y", "")); vz=float(request.form.get("volume_z", ""))
                capacidade=float(request.form.get("capacidade_diaria_horas", ""))
            except (TypeError, ValueError):
                vx=vy=vz=capacidade=0
            latitude = ler_coordenada_formulario(request.form.get("latitude"), -90, 90)
            longitude = ler_coordenada_formulario(request.form.get("longitude"), -180, 180)
            erro = None
            if not nome or not TELEFONE_MIN_DIGITOS_IMPRESSORA <= len(telefone) <= 13:
                erro = "Informe o nome da operação e um telefone válido com DDD."
            elif existente and existente["id"] != impressora["id"]:
                erro = "Este telefone já está vinculado a outro parceiro."
            elif not modelo or tecnologia not in ("fdm", "resina", "ambas"):
                erro = "Informe o modelo e a tecnologia da impressora."
            elif any(not math.isfinite(v) or v < 1 or v > 1000 for v in (vx,vy,vz)):
                erro = "O volume deve ficar entre 1 e 1000 mm por eixo."
            elif not math.isfinite(capacidade) or not 0.5 <= capacidade <= 24:
                erro = "A capacidade diária deve ficar entre 0,5 e 24 horas."
            elif len(endereco) < 8 or not pix_recebimento:
                erro = "Informe o endereço base e a chave Pix para repasses."
            elif (latitude is None) != (longitude is None):
                erro = "Preencha latitude e longitude juntas ou deixe as duas em branco."
            if erro:
                flash(erro)
            else:
                conn.execute(
                    """UPDATE impressoras SET nome=?,telefone=?,modelo_impressora=?,tecnologia=?,volume_x=?,volume_y=?,volume_z=?,
                       capacidade_diaria_horas=?,endereco_base=?,pix_recebimento=?,observacoes_equipamento=?,
                       latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),
                       localizacao_em=CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE localizacao_em END,
                       atualizado_em=CURRENT_TIMESTAMP WHERE id=?""",
                    (nome,telefone,modelo,tecnologia,vx,vy,vz,capacidade,endereco,pix_recebimento,observacoes,
                     latitude,longitude,latitude,impressora["id"]),
                )
                conn.commit(); session["impressora_nome"] = nome; flash("Perfil técnico atualizado.")
        elif acao == "senha":
            atual=request.form.get("senha_atual", ""); nova=request.form.get("nova_senha", ""); confirmar=request.form.get("confirmar_nova_senha", "")
            if not check_password_hash(impressora["senha_hash"], atual): flash("A senha atual não confere.")
            elif not 8 <= len(nova) <= 128: flash("A nova senha precisa ter entre 8 e 128 caracteres.")
            elif nova != confirmar: flash("A confirmação da nova senha não coincide.")
            else:
                conn.execute("UPDATE impressoras SET senha_hash=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=?", (generate_password_hash(nova),impressora["id"]))
                conn.commit(); flash("Senha alterada com segurança.")
        impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    conn.close()
    return render_template("impressora_perfil.html", impressora=impressora)


@app.route("/impressora/materiais", methods=["POST"])
@login_impressora_obrigatorio
def impressora_materiais():
    selecionados = [m for m in request.form.getlist("materiais") if m in MATERIAIS]
    if not selecionados:
        flash("Selecione pelo menos um material para continuar recebendo pedidos compatíveis.")
        return redirect(url_for("impressora_painel"))
    conn = get_db()
    atualizar_materiais_impressora(conn, session["impressora_id"], ",".join(selecionados))
    if buscar_impressora_por_id(conn, session["impressora_id"])["online"]:
        distribuicao.reconsiderar_pedidos_sem_impressora(conn)
    conn.close()
    flash("Materiais atualizados. A fila vai considerar apenas pedidos compatíveis com sua máquina.")
    return redirect(url_for("impressora_painel"))


@app.route("/impressora/status", methods=["POST"])
@login_impressora_obrigatorio
def impressora_status():
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    if not impressora or not impressora["ativo"]:
        conn.close()
        flash("Sua conta ainda não está liberada para receber solicitações. Confira o status no painel ou fale com a Voxxel.")
        return redirect(url_for("impressora_painel"))

    online = request.form.get("online") == "1"
    latitude = ler_coordenada_formulario(request.form.get("latitude"), -90, 90)
    longitude = ler_coordenada_formulario(request.form.get("longitude"), -180, 180)
    if online and latitude is None and longitude is None and impressora["latitude"] is not None and impressora["longitude"] is not None:
        latitude, longitude = impressora["latitude"], impressora["longitude"]
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
    latitude = ler_coordenada_formulario(request.form.get("latitude"), -90, 90)
    longitude = ler_coordenada_formulario(request.form.get("longitude"), -180, 180)
    if latitude is None or longitude is None:
        return {"ok": False}, 400
    conn = get_db()
    atualizar_localizacao_impressora(conn, session["impressora_id"], latitude, longitude)
    conn.close()
    return {"ok": True}


@app.route("/voxxel-sw.js")
def voxxel_service_worker():
    """Service worker raiz para alertas da Rede Voxxel em abas em segundo plano."""
    caminho = os.path.join(app.root_path, "static", "js", "voxxel-sw.js")
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            conteudo = arquivo.read()
    except OSError:
        abort(404)
    resposta = Response(conteudo, mimetype="application/javascript")
    resposta.headers["Service-Worker-Allowed"] = "/"
    resposta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resposta


def _resumo_seguro_oferta(pedido):
    """Resumo pré-aceite sem telefone, endereço ou observações privadas.

    A parceira recebe informação suficiente para decidir se quer analisar o
    trabalho; dados completos ficam disponíveis somente depois da atribuição.
    """
    if pedido["tipo"] == "orcamento":
        resumo = (pedido["descricao_projeto"] or "Projeto personalizado para análise.").strip()
        return resumo[:240]
    linhas = []
    for linha in (pedido["detalhes"] or "").splitlines():
        limpa = linha.strip()
        if not limpa:
            continue
        baixo = limpa.lower()
        if baixo.startswith(("recebimento:", "endereço:", "endereco:", "observações:", "observacoes:")):
            continue
        linhas.append(limpa)
        if len(linhas) >= 3:
            break
    return " · ".join(linhas)[:240] or "Pedido do catálogo para análise."


@app.route("/impressora/api/oferta-atual")
@login_impressora_obrigatorio
def impressora_api_oferta_atual():
    """Retorna a oferta pendente da impressora logada para o aviso global.
    Assim a parceira pode receber uma solicitação em qualquer página do site,
    sem precisar manter o painel aberto."""
    conn = get_db()
    impressora = buscar_impressora_por_id(conn, session["impressora_id"])
    distribuicao.avancar_filas_pendentes(conn)
    if impressora and impressora["online"] and not distribuicao.localizacao_recente(impressora):
        definir_status_impressora(conn, impressora["id"], False)
        impressora = buscar_impressora_por_id(conn, impressora["id"])
    if not impressora or not impressora["ativo"] or not impressora["online"]:
        conn.close()
        return {"ok": True, "oferta": None}

    oferta = distribuicao.oferta_pendente_da_impressora(conn, impressora["id"])
    if not oferta:
        conn.close()
        return {"ok": True, "oferta": None}

    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (oferta["pedido_id"],)).fetchone()
    if not pedido:
        conn.close()
        return {"ok": True, "oferta": None}

    distancia = None
    if (impressora["latitude"] is not None and impressora["longitude"] is not None and
            pedido["cliente_lat"] is not None and pedido["cliente_lng"] is not None):
        distancia = round(distribuicao.haversine_km(
            impressora["latitude"], impressora["longitude"],
            pedido["cliente_lat"], pedido["cliente_lng"]
        ), 1)

    pct = percentual_comissao(conn)
    ganho = round(float(pedido["valor_estimado"] or 0) * (1 - pct / 100), 2)
    material_chave = pedido["material_requisito"] if "material_requisito" in pedido.keys() else None
    material_nome = None
    if material_chave:
        nomes_materiais = [
            MATERIAIS.get(chave.strip().lower(), {}).get("nome", chave.strip().upper())
            for chave in material_chave.split(",") if chave.strip()
        ]
        material_nome = " + ".join(nomes_materiais)
    segundos = distribuicao.segundos_restantes_oferta(oferta)
    dados = {
        "id": oferta["id"],
        "pedido_id": pedido["id"],
        "tipo": "Pedido do catálogo" if pedido["tipo"] == "loja" else "Projeto personalizado",
        "detalhes": _resumo_seguro_oferta(pedido),
        "material": material_nome,
        "distancia_km": distancia,
        "ganho": ganho,
        "valor_pedido": round(float(pedido["valor_estimado"] or 0), 2),
        "segundos_restantes": segundos,
        "referencia_status": pedido["referencia_status"] if "referencia_status" in pedido.keys() else "nao_aplicavel",
    }
    conn.close()
    return {"ok": True, "oferta": dados}


@app.route("/impressora/api/oferta/<int:oferta_id>/responder", methods=["POST"])
@login_impressora_obrigatorio
def impressora_api_oferta_responder(oferta_id):
    acao = request.form.get("acao")
    if acao not in ("aceitar", "recusar"):
        return {"ok": False, "erro": "Ação inválida."}, 400
    conn = get_db()
    aplicado = distribuicao.responder_oferta(conn, oferta_id, session["impressora_id"], acao == "aceitar")
    conn.close()
    if not aplicado:
        return {"ok": False, "erro": "Essa oferta não está mais disponível."}, 409
    return {"ok": True, "acao": acao}


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
        flash("Solicitação aceita para análise. Revise as referências e alinhe dúvidas com o cliente antes de produzir.")
    else:
        flash("Solicitação recusada. A Rede Voxxel seguirá buscando outro parceiro compatível.")
    return redirect(url_for("impressora_painel"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Por padrão, debug fica DESLIGADO -- precisa ligar explicitamente em
    # desenvolvimento. Rodar com o debugger do Werkzeug ligado em produção é
    # uma falha grave de segurança: ele permite executar código arbitrário
    # no servidor pra quem encontrar uma página de erro.
    debug = os.environ.get("VOXXEL_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
