import os
import re
import time
import sqlite3
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

DB_PATH = Path(__file__).parent / "voxxel.db"

# Se a variável DATABASE_URL existir (o Render seta ela sozinho quando você
# liga um banco Postgres ao serviço), usamos Postgres. Sem ela, usamos
# SQLite local -- útil pra testar no seu computador sem instalar Postgres.
DATABASE_URL = os.environ.get("DATABASE_URL")
USING_POSTGRES = bool(DATABASE_URL)

if USING_POSTGRES:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    def _url_com_ssl(url):
        """Garante sslmode=require na URL de conexão -- o Postgres do Render
        aceita e recomenda SSL tanto na URL interna quanto na externa, e
        isso evita erro de conexão recusada em provedores que exigem SSL."""
        url = url.replace("postgres://", "postgresql://", 1)
        partes = urlparse(url)
        query = parse_qs(partes.query)
        query.setdefault("sslmode", ["require"])
        nova_query = urlencode(query, doseq=True)
        return urlunparse(partes._replace(query=nova_query))

    _DATABASE_URL_SSL = _url_com_ssl(DATABASE_URL)

    # Pool pequeno de propósito: o plano free do Postgres no Render permite
    # poucas conexões simultâneas, e o Procfile sobe só 1 worker do gunicorn
    # por padrão. Se um dia você aumentar os workers (`gunicorn -w N`),
    # lembre de manter POOL_MAX * N dentro do limite do seu plano Postgres.
    POOL_MIN, POOL_MAX = 1, 5
    _pool = None

    def _obter_pool():
        global _pool
        if _pool is None:
            ultimo_erro = None
            for tentativa in range(1, 4):
                try:
                    _pool = psycopg2.pool.ThreadedConnectionPool(
                        POOL_MIN, POOL_MAX, _DATABASE_URL_SSL,
                        cursor_factory=psycopg2.extras.RealDictCursor,
                    )
                    break
                except psycopg2.OperationalError as erro:
                    ultimo_erro = erro
                    time.sleep(0.6 * tentativa)  # banco pode estar "acordando"
            else:
                raise RuntimeError(
                    "Não foi possível conectar ao Postgres (DATABASE_URL). "
                    "Confira se a variável está correta e se o banco está "
                    f"com status 'Available' no Render. Erro original: {ultimo_erro}"
                )
        return _pool

CATEGORIAS = {
    "tecnica": "Peça Técnica",
    "cosplay": "Cosplay & Acessório",
    "decoracao": "Decoração & Utilitário",
}

CONFIG_PADRAO = {
    "pix_chave": "",
    "pix_nome": "Voxxel Impressao 3D",
    "pix_cidade": "Sao Jose dos Pinhais",
    "whatsapp": "5541997193025",
    "mp_access_token": "",
    "vendedor_nome": "Voxxel",
    # % que a Voxxel retém sobre o valor de cada pedido atribuído a uma
    # impressora parceira (marketplace) -- editável pelo admin. Pedidos
    # que a própria Voxxel produz (sem impressora parceira) não têm
    # comissão, é 100% dela mesma.
    "comissao_percentual": "15",
}

PRODUTOS_SEED = [
    ("Suporte Geométrico para Plantas", "decoracao", 59.90, "Vaso facetado em PLA, acabamento fosco.", "15deg"),
    ("Porta Talheres Poligonal", "decoracao", 44.90, "Organizador de bancada com design low poly.", "80deg"),
    ("Máscara Cosplay Cavaleiro", "cosplay", 129.90, "Réplica pronta para pintura, tamanho único.", "150deg"),
    ("Suporte de Celular Articulado", "tecnica", 34.90, "Peça técnica ajustável para mesa.", "220deg"),
    ("Escultura Facetada de Mesa", "decoracao", 39.90, "Peça geométrica decorativa colecionável.", "270deg"),
    ("Organizador de Ferramentas", "tecnica", 49.90, "Suporte modular para bancada de trabalho.", "320deg"),
    ("Punho de Manopla Infinity", "cosplay", 139.90, "Réplica de manopla, montagem em partes.", "45deg"),
    ("Porta-Caneta Poligonal", "decoracao", 29.90, "Organizador de mesa com faces geométricas.", "190deg"),
    ("Suporte para Fones", "tecnica", 32.90, "Suporte de bancada para headset.", "300deg"),
]


class _Connection:
    """Encapsula sqlite3 ou psycopg2 atrás da mesma interface usada no app.py
    (conn.execute(sql, params).fetchone()/.fetchall(), conn.commit(), conn.close()).
    Os placeholders no app.py usam '?' (estilo sqlite); aqui convertemos para
    '%s' automaticamente quando estamos no Postgres.

    No Postgres, a conexão vem de um pool reaproveitável -- abrir uma conexão
    TCP nova a cada requisição é caro e desperdiça o limite (baixo) de
    conexões simultâneas dos planos free. close() devolve a conexão pro
    pool em vez de encerrá-la de verdade.
    """

    def __init__(self):
        if USING_POSTGRES:
            self._pool = _obter_pool()
            ultimo_erro = None
            for tentativa in range(1, 4):
                try:
                    self._conn = self._pool.getconn()
                    self._conn.cursor().execute("SELECT 1")  # detecta conexão morta
                    self._conn.commit()
                    break
                except psycopg2.OperationalError as erro:
                    ultimo_erro = erro
                    try:
                        self._pool.putconn(self._conn, close=True)
                    except Exception:
                        pass
                    time.sleep(0.4 * tentativa)
            else:
                raise RuntimeError(f"Conexão com o Postgres falhou: {ultimo_erro}")
        else:
            self._conn = sqlite3.connect(DB_PATH)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        if USING_POSTGRES:
            sql = sql.replace("?", "%s")
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        if USING_POSTGRES:
            sql = sql.replace("?", "%s")
        cur.executemany(sql, seq_of_params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        if USING_POSTGRES:
            self._pool.putconn(self._conn)
        else:
            self._conn.close()


def get_db():
    return _Connection()


def to_blob(dados):
    """Prepara bytes de imagem pro formato que o driver do banco espera.
    No Postgres, bytea precisa ser explicitamente adaptado; no SQLite,
    bytes puros já funcionam."""
    if dados is None:
        return None
    if USING_POSTGRES:
        return psycopg2.Binary(dados)
    return dados


def init_db():
    # precisa ser checado ANTES de abrir a conexão: sqlite3.connect() já cria
    # o arquivo do banco no disco, então depois disso DB_PATH.exists() sempre
    # seria True.
    is_new_sqlite = not USING_POSTGRES and not DB_PATH.exists()
    conn = get_db()

    if USING_POSTGRES:
        # Lock consultivo: se um dia o gunicorn subir com mais de 1 worker,
        # isso evita que dois processos criem as tabelas/semeiem os produtos
        # de exemplo ao mesmo tempo (condição de corrida na primeira subida).
        # O número é arbitrário, só precisa ser o mesmo em toda a aplicação.
        conn.execute("SELECT pg_advisory_lock(913042)")
    try:
        _criar_tabelas(conn, is_new_sqlite)
    finally:
        if USING_POSTGRES:
            conn.execute("SELECT pg_advisory_unlock(913042)")
            conn.commit()
    conn.close()


def _criar_tabelas(conn, is_new_sqlite):
    if USING_POSTGRES:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                categoria TEXT NOT NULL,
                preco REAL NOT NULL,
                descricao TEXT DEFAULT '',
                imagem_ang TEXT DEFAULT '0deg',
                ativo INTEGER DEFAULT 1
            )
            """
        )
        conn.commit()
        # Migração: adiciona colunas novas se o banco já existia antes delas
        # (não afeta bancos criados do zero).
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS imagem_dados BYTEA")
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS imagem_mimetype TEXT")
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS estoque INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pedidos (
                id SERIAL PRIMARY KEY,
                tipo TEXT NOT NULL,
                criado_em TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'),
                detalhes TEXT NOT NULL,
                valor_estimado REAL NOT NULL,
                status TEXT DEFAULT 'novo'
            )
            """
        )
        conn.commit()
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_nome TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_telefone TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS forma_pagamento TEXT DEFAULT 'combinar'")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS status_pagamento TEXT DEFAULT 'aguardando'")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS mp_preference_id TEXT")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS mp_payment_id TEXT")
        # Marketplace: quanto a Voxxel ganha desse pedido específico (só é
        # preenchido quando o pedido é atribuído a uma impressora parceira
        # -- ver distribuicao.py). Fica NULL pra pedidos que a Voxxel
        # mesma produz.
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS comissao_voxxel REAL")
        # Marketplace de impressão: localização do cliente (pra achar a
        # impressora mais próxima) e o estado da fila de despacho (veja
        # distribuicao.py). `impressora_id` é adicionada mais abaixo, com
        # a referência, depois que a tabela `impressoras` existir.
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_lat REAL")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_lng REAL")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS distribuicao_status TEXT DEFAULT 'nao_aplicavel'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS configuracoes (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
            """
        )
        conn.commit()

        # Contas de cliente (login por telefone) -- pedidos feitos antes
        # dessa tabela existir ficam com cliente_id NULO (pedido "avulso",
        # continua acessível pelo link direto de pagamento).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                telefone TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                criado_em TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')
            )
            """
        )
        conn.commit()

        # Impressoras parceiras (o lado "entregador" do marketplace): cada
        # uma tem login próprio (telefone + senha, igual ao cliente) e uma
        # localização GPS que ela mesma atualiza ao ficar online.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS impressoras (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                telefone TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                localizacao_em TEXT,
                online INTEGER DEFAULT 0,
                ativo INTEGER DEFAULT 1,
                criado_em TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')
            )
            """
        )
        conn.commit()

        # Histórico de ofertas de cada pedido pra cada impressora -- é
        # essa tabela que guarda quem já recusou o quê, pra fila de
        # despacho não oferecer de novo pra quem já disse não.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ofertas_impressao (
                id SERIAL PRIMARY KEY,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
                impressora_id INTEGER NOT NULL REFERENCES impressoras(id),
                status TEXT DEFAULT 'pendente',
                criado_em TEXT,
                respondido_em TEXT
            )
            """
        )
        conn.commit()
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_id INTEGER REFERENCES clientes(id)")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS impressora_id INTEGER REFERENCES impressoras(id)")
        conn.commit()

        # Popula os produtos de exemplo só se a tabela ainda estiver vazia
        row = conn.execute("SELECT COUNT(*) AS total FROM produtos").fetchone()
        precisa_seed = row["total"] == 0
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                categoria TEXT NOT NULL,
                preco REAL NOT NULL,
                descricao TEXT DEFAULT '',
                imagem_ang TEXT DEFAULT '0deg',
                ativo INTEGER DEFAULT 1
            )
            """
        )
        # Migração: adiciona colunas novas se o banco local já existia antes
        # delas (ignora erro se a coluna já existir).
        for coluna_sql in (
            "ALTER TABLE produtos ADD COLUMN imagem_dados BLOB",
            "ALTER TABLE produtos ADD COLUMN imagem_mimetype TEXT",
            "ALTER TABLE produtos ADD COLUMN estoque INTEGER",
        ):
            try:
                conn.execute(coluna_sql)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                detalhes TEXT NOT NULL,
                valor_estimado REAL NOT NULL,
                status TEXT DEFAULT 'novo'
            )
            """
        )
        for coluna_sql in (
            "ALTER TABLE pedidos ADD COLUMN cliente_nome TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN cliente_telefone TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN forma_pagamento TEXT DEFAULT 'combinar'",
            "ALTER TABLE pedidos ADD COLUMN status_pagamento TEXT DEFAULT 'aguardando'",
            "ALTER TABLE pedidos ADD COLUMN mp_preference_id TEXT",
            "ALTER TABLE pedidos ADD COLUMN mp_payment_id TEXT",
            # Marketplace de impressão (ver distribuicao.py)
            "ALTER TABLE pedidos ADD COLUMN cliente_lat REAL",
            "ALTER TABLE pedidos ADD COLUMN cliente_lng REAL",
            "ALTER TABLE pedidos ADD COLUMN distribuicao_status TEXT DEFAULT 'nao_aplicavel'",
            "ALTER TABLE pedidos ADD COLUMN comissao_voxxel REAL",
        ):
            try:
                conn.execute(coluna_sql)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS configuracoes (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
            """
        )

        # Contas de cliente (login por telefone) -- pedidos feitos antes
        # dessa tabela existir ficam com cliente_id NULO (pedido "avulso",
        # continua acessível pelo link direto de pagamento).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                telefone TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.execute("ALTER TABLE pedidos ADD COLUMN cliente_id INTEGER REFERENCES clientes(id)")
        except sqlite3.OperationalError:
            pass
        conn.commit()

        # Impressoras parceiras (o lado "entregador" do marketplace): cada
        # uma tem login próprio (telefone + senha, igual ao cliente) e uma
        # localização GPS que ela mesma atualiza ao ficar online.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS impressoras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                telefone TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                localizacao_em TEXT,
                online INTEGER DEFAULT 0,
                ativo INTEGER DEFAULT 1,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Histórico de ofertas de cada pedido pra cada impressora -- é essa
        # tabela que guarda quem já recusou o quê, pra fila de despacho não
        # oferecer de novo pra quem já disse não.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ofertas_impressao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
                impressora_id INTEGER NOT NULL REFERENCES impressoras(id),
                status TEXT DEFAULT 'pendente',
                criado_em TEXT,
                respondido_em TEXT
            )
            """
        )
        conn.commit()
        try:
            conn.execute("ALTER TABLE pedidos ADD COLUMN impressora_id INTEGER REFERENCES impressoras(id)")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        precisa_seed = is_new_sqlite

    if precisa_seed:
        conn.executemany(
            "INSERT INTO produtos (nome, categoria, preco, descricao, imagem_ang) VALUES (?, ?, ?, ?, ?)",
            PRODUTOS_SEED,
        )
        conn.commit()

    # Índices pra manter as listagens rápidas conforme o catálogo/pedidos
    # crescem (o filtro por categoria e por status são os mais usados).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_produtos_categoria_ativo ON produtos(categoria, ativo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos(cliente_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_pedido ON ofertas_impressao(pedido_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_impressora ON ofertas_impressao(impressora_id, status)")
    conn.commit()

    # Garante que toda chave de configuração padrão exista (não sobrescreve
    # o que o admin já tiver salvo).
    existentes = {row["chave"] for row in conn.execute("SELECT chave FROM configuracoes").fetchall()}
    faltando = [(chave, valor) for chave, valor in CONFIG_PADRAO.items() if chave not in existentes]
    if faltando:
        conn.executemany("INSERT INTO configuracoes (chave, valor) VALUES (?, ?)", faltando)
        conn.commit()


def criar_pedido(conn, tipo, detalhes, valor_estimado, cliente_nome="", cliente_telefone="",
                  forma_pagamento="combinar", cliente_id=None, cliente_lat=None, cliente_lng=None):
    """Insere um pedido (venda da loja ou orçamento) e devolve o id gerado,
    já lidando com a diferença de sintaxe entre SQLite e Postgres.
    `cliente_id` liga o pedido à conta logada -- fica None só para pedidos
    antigos, de antes de existir login (checkout hoje exige conta).
    `cliente_lat`/`cliente_lng` vêm da geolocalização do navegador (podem
    vir None se o cliente não permitiu) -- usados por distribuicao.py pra
    achar a impressora mais próxima."""
    params = (
        tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone, forma_pagamento,
        cliente_id, cliente_lat, cliente_lng,
    )
    if USING_POSTGRES:
        cur = conn.execute(
            """INSERT INTO pedidos (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone,
                                     forma_pagamento, cliente_id, cliente_lat, cliente_lng)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            params,
        )
        novo_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            """INSERT INTO pedidos (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone,
                                     forma_pagamento, cliente_id, cliente_lat, cliente_lng)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        novo_id = cur.lastrowid
    conn.commit()
    return novo_id


def ler_coordenada_formulario(valor):
    """Converte o valor de latitude/longitude vindo do formulário (campo
    hidden preenchido por JS) pra float, ou None se estiver vazio/inválido
    -- cliente pode ter negado a permissão de localização."""
    try:
        if valor is None or str(valor).strip() == "":
            return None
        return float(valor)
    except (TypeError, ValueError):
        return None


# ---------- impressoras parceiras (marketplace) ----------

def criar_impressora(conn, nome, telefone, senha_hash):
    telefone = normalizar_telefone(telefone)
    if USING_POSTGRES:
        cur = conn.execute(
            "INSERT INTO impressoras (nome, telefone, senha_hash) VALUES (?, ?, ?) RETURNING id",
            (nome, telefone, senha_hash),
        )
        novo_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            "INSERT INTO impressoras (nome, telefone, senha_hash) VALUES (?, ?, ?)",
            (nome, telefone, senha_hash),
        )
        novo_id = cur.lastrowid
    conn.commit()
    return novo_id


def buscar_impressora_por_telefone(conn, telefone):
    telefone = normalizar_telefone(telefone)
    return conn.execute("SELECT * FROM impressoras WHERE telefone = ?", (telefone,)).fetchone()


def buscar_impressora_por_id(conn, impressora_id):
    return conn.execute("SELECT * FROM impressoras WHERE id = ?", (impressora_id,)).fetchone()


def listar_impressoras(conn):
    return conn.execute("SELECT * FROM impressoras ORDER BY id DESC").fetchall()


def definir_status_impressora(conn, impressora_id, online, latitude=None, longitude=None):
    """Liga/desliga a impressora. Ao ficar online, também grava a
    localização atual (o navegador manda junto nesse momento); ao ficar
    offline não mexe na localização salva -- fica guardada pra próxima vez."""
    if online and latitude is not None and longitude is not None:
        conn.execute(
            "UPDATE impressoras SET online = 1, latitude = ?, longitude = ?, localizacao_em = ? WHERE id = ?",
            (latitude, longitude, time.strftime("%Y-%m-%d %H:%M:%S"), impressora_id),
        )
    else:
        conn.execute(
            "UPDATE impressoras SET online = ? WHERE id = ?",
            (1 if online else 0, impressora_id),
        )
    conn.commit()


def atualizar_localizacao_impressora(conn, impressora_id, latitude, longitude):
    """Ping periódico enviado pelo painel enquanto a impressora está
    online, pra manter a posição atualizada mesmo que ela se desloque."""
    conn.execute(
        "UPDATE impressoras SET latitude = ?, longitude = ?, localizacao_em = ? WHERE id = ? AND online = 1",
        (latitude, longitude, time.strftime("%Y-%m-%d %H:%M:%S"), impressora_id),
    )
    conn.commit()


def definir_impressora_ativa(conn, impressora_id, ativo):
    """Usado pelo admin pra bloquear/desbloquear uma impressora parceira
    (ela para de receber ofertas novas, mas o histórico dela continua)."""
    conn.execute("UPDATE impressoras SET ativo = ?, online = 0 WHERE id = ?", (1 if ativo else 0, impressora_id))
    conn.commit()


def listar_pedidos_da_impressora(conn, impressora_id):
    return conn.execute(
        "SELECT * FROM pedidos WHERE impressora_id = ? ORDER BY id DESC", (impressora_id,)
    ).fetchall()


def atualizar_perfil_impressora(conn, impressora_id, nome, telefone):
    """Atualiza nome/telefone da própria impressora parceira (editado por
    ela mesma no painel). O telefone é normalizado igual no cadastro/login,
    pra manter tudo consistente."""
    telefone = normalizar_telefone(telefone)
    conn.execute(
        "UPDATE impressoras SET nome = ?, telefone = ? WHERE id = ?",
        (nome, telefone, impressora_id),
    )
    conn.commit()


def atualizar_senha_impressora(conn, impressora_id, senha_hash):
    conn.execute(
        "UPDATE impressoras SET senha_hash = ? WHERE id = ?",
        (senha_hash, impressora_id),
    )
    conn.commit()


def normalizar_telefone(telefone):
    """Mantém só os dígitos do telefone -- assim '(41) 99852-6355' e
    '41999826355' são tratados como o mesmo valor no login/cadastro."""
    return re.sub(r"\D", "", telefone or "")


def criar_cliente(conn, nome, telefone, senha_hash):
    """Cria uma conta de cliente. Assume que já foi checado antes que esse
    telefone ainda não tem conta (evita corrida óbvia num site pequeno;
    a coluna UNIQUE no banco é a garantia final contra duplicidade)."""
    telefone = normalizar_telefone(telefone)
    if USING_POSTGRES:
        cur = conn.execute(
            "INSERT INTO clientes (nome, telefone, senha_hash) VALUES (?, ?, ?) RETURNING id",
            (nome, telefone, senha_hash),
        )
        novo_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            "INSERT INTO clientes (nome, telefone, senha_hash) VALUES (?, ?, ?)",
            (nome, telefone, senha_hash),
        )
        novo_id = cur.lastrowid
    conn.commit()
    return novo_id


def buscar_cliente_por_telefone(conn, telefone):
    telefone = normalizar_telefone(telefone)
    return conn.execute("SELECT * FROM clientes WHERE telefone = ?", (telefone,)).fetchone()


def buscar_cliente_por_id(conn, cliente_id):
    return conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()


def listar_pedidos_cliente(conn, cliente_id):
    return conn.execute(
        "SELECT * FROM pedidos WHERE cliente_id = ? ORDER BY id DESC", (cliente_id,)
    ).fetchall()


# ---------- comissão do marketplace ----------

def percentual_comissao(conn):
    """Lê a % de comissão configurada pelo admin (chave 'comissao_percentual'),
    com um padrão seguro de 15% caso o valor salvo esteja vazio ou inválido."""
    valor = conn.execute(
        "SELECT valor FROM configuracoes WHERE chave = 'comissao_percentual'"
    ).fetchone()
    try:
        pct = float(valor["valor"]) if valor else 15.0
    except (TypeError, ValueError):
        pct = 15.0
    return max(0.0, min(pct, 100.0))


def aplicar_comissao_pedido(conn, pedido_id, valor_estimado):
    """Calcula e grava a comissão da Voxxel sobre um pedido no momento em
    que ele é atribuído a uma impressora parceira. Devolve o valor
    calculado (R$) pra quem quiser usar/exibir na hora."""
    pct = percentual_comissao(conn)
    comissao = round(float(valor_estimado) * pct / 100, 2)
    conn.execute(
        "UPDATE pedidos SET comissao_voxxel = ? WHERE id = ?",
        (comissao, pedido_id),
    )
    return comissao


def resumo_comissoes(conn):
    """Total acumulado de comissão da Voxxel sobre pedidos já atribuídos a
    impressoras parceiras, e o detalhamento por impressora -- pra exibir
    no painel do admin (quanto o marketplace já rendeu, e quem gerou mais)."""
    total = conn.execute(
        "SELECT COALESCE(SUM(comissao_voxxel), 0) AS total FROM pedidos WHERE comissao_voxxel IS NOT NULL"
    ).fetchone()["total"]
    por_impressora = conn.execute(
        """SELECT i.id, i.nome, COUNT(p.id) AS pedidos,
                  COALESCE(SUM(p.comissao_voxxel), 0) AS comissao_total,
                  COALESCE(SUM(p.valor_estimado), 0) AS faturamento_total
           FROM impressoras i
           JOIN pedidos p ON p.impressora_id = i.id AND p.comissao_voxxel IS NOT NULL
           GROUP BY i.id, i.nome
           ORDER BY comissao_total DESC"""
    ).fetchall()
    return {"total": round(total, 2), "por_impressora": por_impressora}


def get_configs(conn):
    """Devolve um dict com todas as configurações da loja (chave -> valor),
    já com os padrões aplicados por baixo caso alguma chave esteja ausente."""
    config = dict(CONFIG_PADRAO)
    for row in conn.execute("SELECT chave, valor FROM configuracoes").fetchall():
        config[row["chave"]] = row["valor"]
    return config


def set_configs(conn, valores):
    """Salva/atualiza várias chaves de configuração de uma vez.
    `valores` é um dict {chave: valor}."""
    for chave, valor in valores.items():
        if USING_POSTGRES:
            conn.execute(
                """INSERT INTO configuracoes (chave, valor) VALUES (?, ?)
                   ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor""",
                (chave, valor),
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES (?, ?)",
                (chave, valor),
            )
    conn.commit()
