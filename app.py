import os
import json
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, Response

from database import init_db, get_db, CATEGORIAS, to_blob
from calculadora import calcular_orcamento, formatar_horas, MATERIAIS, QUALIDADE, COMPLEXIDADE

ADMIN_PASSWORD = os.environ.get("VOXXEL_ADMIN_PASSWORD", "voxxel123")
WHATSAPP_NUMERO = "5541998526355"

TIPOS_IMAGEM_PERMITIDOS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

app = Flask(__name__)
app.secret_key = os.environ.get("VOXXEL_SECRET_KEY", "troque-esta-chave-em-producao")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # limite de 5MB por upload

with app.app_context():
    init_db()


@app.errorhandler(413)
def imagem_grande_demais(e):
    flash("A imagem enviada é grande demais. O limite é 5MB.")
    return redirect(request.referrer or url_for("admin_produtos"))


# ---------- helpers ----------

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


def admin_requerido():
    return session.get("admin_logado", False)


@app.context_processor
def inject_globals():
    qtd_carrinho = sum(carrinho_sessao().values())
    return dict(categorias=CATEGORIAS, whatsapp_numero=WHATSAPP_NUMERO, qtd_carrinho=qtd_carrinho)


# ---------- páginas públicas ----------

@app.route("/")
def home():
    return render_template("index.html")


# Colunas usadas nas listagens (loja e admin): evita carregar o BLOB da
# imagem inteiro só pra mostrar a lista de produtos -- só um indicador
# booleano (tem_imagem) que a rota /produto/<id>/imagem resolve de verdade.
COLUNAS_PRODUTO_LISTA = """
    id, nome, categoria, preco, descricao, imagem_ang, ativo,
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


@app.route("/carrinho/adicionar/<int:produto_id>", methods=["POST"])
def carrinho_adicionar(produto_id):
    carrinho = carrinho_sessao()
    pid = str(produto_id)
    carrinho[pid] = carrinho.get(pid, 0) + 1
    session["carrinho"] = carrinho
    session.modified = True
    flash("Produto adicionado ao carrinho.")
    return redirect(request.referrer or url_for("loja"))


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


@app.route("/carrinho/finalizar", methods=["POST"])
def carrinho_finalizar():
    conn = get_db()
    itens, total = carrinho_detalhado(conn)
    if not itens:
        conn.close()
        return redirect(url_for("loja"))

    linhas = [f"{i['qtd']}x {i['produto']['nome']} - R$ {i['subtotal']:.2f}".replace(".", ",") for i in itens]
    detalhes = "\n".join(linhas)

    conn.execute(
        "INSERT INTO pedidos (tipo, detalhes, valor_estimado) VALUES (?, ?, ?)",
        ("loja", detalhes, total),
    )
    conn.commit()
    conn.close()

    session["carrinho"] = {}
    session.modified = True

    msg = "Olá! Quero fechar esse pedido na Voxxel 🙂%0A%0A"
    msg += "%0A".join(l.replace(" ", "%20") for l in linhas)
    msg += f"%0A%0A*Total:* R$ {total:.2f}".replace(".", ",")
    return redirect(f"https://wa.me/{WHATSAPP_NUMERO}?text={msg}")


@app.route("/orcamento", methods=["GET", "POST"])
def orcamento():
    resultado = None
    form = {
        "categoria": "tecnica", "altura": 10, "largura": 10, "profundidade": 10,
        "quantidade": 1, "material": "pla", "qualidade": "padrao", "complexidade": "media",
    }

    if request.method == "POST":
        form.update({
            "categoria": request.form.get("categoria", "tecnica"),
            "altura": float(request.form.get("altura") or 0),
            "largura": float(request.form.get("largura") or 0),
            "profundidade": float(request.form.get("profundidade") or 0),
            "quantidade": int(request.form.get("quantidade") or 1),
            "material": request.form.get("material", "pla"),
            "qualidade": request.form.get("qualidade", "padrao"),
            "complexidade": request.form.get("complexidade", "media"),
        })
        resultado = calcular_orcamento(
            form["altura"], form["largura"], form["profundidade"], form["quantidade"],
            form["categoria"], form["complexidade"], form["material"], form["qualidade"],
        )
        resultado["tempo_formatado"] = formatar_horas(resultado["horas_total"])

        if request.form.get("acao") == "enviar":
            conn = get_db()
            detalhes = (
                f"Categoria: {resultado['categoria_nome']}\n"
                f"Dimensões: {form['altura']}x{form['largura']}x{form['profundidade']} cm\n"
                f"Material: {resultado['material_nome']}\n"
                f"Qualidade: {form['qualidade']}\n"
                f"Quantidade: {form['quantidade']}"
            )
            conn.execute(
                "INSERT INTO pedidos (tipo, detalhes, valor_estimado) VALUES (?, ?, ?)",
                ("orcamento", detalhes, resultado["preco_total"]),
            )
            conn.commit()
            conn.close()

            msg = (
                "Olá! Quero um orçamento na Voxxel 🙂%0A%0A"
                f"*Categoria:* {resultado['categoria_nome']}%0A"
                f"*Dimensões:* {form['altura']}x{form['largura']}x{form['profundidade']} cm%0A"
                f"*Material:* {resultado['material_nome']}%0A"
                f"*Qualidade:* {form['qualidade']}%0A"
                f"*Quantidade:* {form['quantidade']}%0A"
                f"*Estimativa automática:* R$ {resultado['preco_total']:.2f}".replace(".", ",")
            )
            return redirect(f"https://wa.me/{WHATSAPP_NUMERO}?text={msg}")

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
    if request.method == "POST":
        if request.form.get("senha") == ADMIN_PASSWORD:
            session["admin_logado"] = True
            return redirect(url_for("admin_produtos"))
        erro = "Senha incorreta."
    return render_template("admin_login.html", erro=erro)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logado", None)
    return redirect(url_for("home"))


@app.route("/admin/produtos")
def admin_produtos():
    if not admin_requerido():
        return redirect(url_for("admin_login"))
    conn = get_db()
    produtos = conn.execute(f"SELECT {COLUNAS_PRODUTO_LISTA} FROM produtos ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin_produtos.html", produtos=produtos)


def processar_upload_imagem(arquivo):
    """Lê o arquivo enviado no formulário e valida formato/conteúdo.
    Retorna (bytes, mimetype) ou None se não veio nenhum arquivo."""
    if not arquivo or not arquivo.filename:
        return None
    if arquivo.mimetype not in TIPOS_IMAGEM_PERMITIDOS:
        flash("Formato de imagem não suportado. Envie um JPG, PNG ou WEBP.")
        return None
    dados = arquivo.read()
    if not dados:
        return None
    return dados, arquivo.mimetype


@app.route("/admin/produtos/novo", methods=["GET", "POST"])
def admin_produto_novo():
    if not admin_requerido():
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        conn = get_db()
        resultado_imagem = processar_upload_imagem(request.files.get("imagem"))
        imagem_dados, imagem_mimetype = resultado_imagem if resultado_imagem else (None, None)
        conn.execute(
            """INSERT INTO produtos
               (nome, categoria, preco, descricao, imagem_ang, ativo, imagem_dados, imagem_mimetype)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.form["nome"], request.form["categoria"], float(request.form["preco"]),
                request.form.get("descricao", ""), request.form.get("imagem_ang", "0deg"),
                1 if request.form.get("ativo") else 0,
                to_blob(imagem_dados), imagem_mimetype,
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_produtos"))
    return render_template("admin_produto_form.html", produto=None)


@app.route("/admin/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
def admin_produto_editar(produto_id):
    if not admin_requerido():
        return redirect(url_for("admin_login"))
    conn = get_db()
    if request.method == "POST":
        resultado_imagem = processar_upload_imagem(request.files.get("imagem"))

        if resultado_imagem:
            # Nova imagem enviada: substitui a anterior
            imagem_dados, imagem_mimetype = resultado_imagem
            conn.execute(
                """UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?,
                   imagem_dados=?, imagem_mimetype=? WHERE id=?""",
                (
                    request.form["nome"], request.form["categoria"], float(request.form["preco"]),
                    request.form.get("descricao", ""), request.form.get("imagem_ang", "0deg"),
                    1 if request.form.get("ativo") else 0,
                    to_blob(imagem_dados), imagem_mimetype, produto_id,
                ),
            )
        elif request.form.get("remover_imagem"):
            # Usuário marcou pra remover a imagem atual, sem enviar outra
            conn.execute(
                """UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=?,
                   imagem_dados=NULL, imagem_mimetype=NULL WHERE id=?""",
                (
                    request.form["nome"], request.form["categoria"], float(request.form["preco"]),
                    request.form.get("descricao", ""), request.form.get("imagem_ang", "0deg"),
                    1 if request.form.get("ativo") else 0, produto_id,
                ),
            )
        else:
            # Mantém a imagem que já existia
            conn.execute(
                "UPDATE produtos SET nome=?, categoria=?, preco=?, descricao=?, imagem_ang=?, ativo=? WHERE id=?",
                (
                    request.form["nome"], request.form["categoria"], float(request.form["preco"]),
                    request.form.get("descricao", ""), request.form.get("imagem_ang", "0deg"),
                    1 if request.form.get("ativo") else 0, produto_id,
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
def admin_produto_excluir(produto_id):
    if not admin_requerido():
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_produtos"))


@app.route("/admin/pedidos")
def admin_pedidos():
    if not admin_requerido():
        return redirect(url_for("admin_login"))
    conn = get_db()
    pedidos = conn.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin_pedidos.html", pedidos=pedidos)


@app.route("/admin/pedidos/<int:pedido_id>/status", methods=["POST"])
def admin_pedido_status(pedido_id):
    if not admin_requerido():
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute("UPDATE pedidos SET status = ? WHERE id = ?", (request.form["status"], pedido_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_pedidos"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("VOXXEL_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
