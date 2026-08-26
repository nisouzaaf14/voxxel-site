import os
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
    "whatsapp": "5541998526355",
    "mp_access_token": "",
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS configuracoes (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
            """
        )
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
    conn.commit()

    # Garante que toda chave de configuração padrão exista (não sobrescreve
    # o que o admin já tiver salvo).
    existentes = {row["chave"] for row in conn.execute("SELECT chave FROM configuracoes").fetchall()}
    faltando = [(chave, valor) for chave, valor in CONFIG_PADRAO.items() if chave not in existentes]
    if faltando:
        conn.executemany("INSERT INTO configuracoes (chave, valor) VALUES (?, ?)", faltando)
        conn.commit()


def criar_pedido(conn, tipo, detalhes, valor_estimado, cliente_nome="", cliente_telefone="", forma_pagamento="combinar"):
    """Insere um pedido (venda da loja ou orçamento) e devolve o id gerado,
    já lidando com a diferença de sintaxe entre SQLite e Postgres."""
    params = (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone, forma_pagamento)
    if USING_POSTGRES:
        cur = conn.execute(
            """INSERT INTO pedidos (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone, forma_pagamento)
               VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
            params,
        )
        novo_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            """INSERT INTO pedidos (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone, forma_pagamento)
               VALUES (?, ?, ?, ?, ?, ?)""",
            params,
        )
        novo_id = cur.lastrowid
    conn.commit()
    return novo_id


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
