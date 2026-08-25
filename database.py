import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "voxxel.db"

# Se a variável DATABASE_URL existir (o Render seta ela sozinho quando você
# liga um banco Postgres ao serviço), usamos Postgres. Sem ela, usamos
# SQLite local -- útil pra testar no seu computador sem instalar Postgres.
DATABASE_URL = os.environ.get("DATABASE_URL")
USING_POSTGRES = bool(DATABASE_URL)

if USING_POSTGRES:
    import psycopg2
    import psycopg2.extras

CATEGORIAS = {
    "tecnica": "Peça Técnica",
    "cosplay": "Cosplay & Acessório",
    "decoracao": "Decoração & Utilitário",
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
    """

    def __init__(self):
        if USING_POSTGRES:
            url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            self._conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
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
        # Migração: adiciona as colunas de imagem se o banco já existia antes
        # dessa funcionalidade (não afeta bancos criados do zero).
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS imagem_dados BYTEA")
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS imagem_mimetype TEXT")
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
        # Migração: adiciona as colunas de imagem se o banco local já existia
        # antes dessa funcionalidade (ignora erro se a coluna já existir).
        for coluna_sql in (
            "ALTER TABLE produtos ADD COLUMN imagem_dados BLOB",
            "ALTER TABLE produtos ADD COLUMN imagem_mimetype TEXT",
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
        conn.commit()
        precisa_seed = is_new_sqlite

    if precisa_seed:
        conn.executemany(
            "INSERT INTO produtos (nome, categoria, preco, descricao, imagem_ang) VALUES (?, ?, ?, ?, ?)",
            PRODUTOS_SEED,
        )
        conn.commit()

    conn.close()
