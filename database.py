import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "voxxel.db"

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


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    is_new = not DB_PATH.exists()
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT NOT NULL,
            preco REAL NOT NULL,
            descricao TEXT DEFAULT '',
            imagem_ang TEXT DEFAULT '0deg',
            ativo INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,               -- 'loja' ou 'orcamento'
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            detalhes TEXT NOT NULL,           -- texto legível do pedido
            valor_estimado REAL NOT NULL,
            status TEXT DEFAULT 'novo'        -- novo, em andamento, concluido
        );
        """
    )
    conn.commit()

    if is_new:
        conn.executemany(
            "INSERT INTO produtos (nome, categoria, preco, descricao, imagem_ang) VALUES (?, ?, ?, ?, ?)",
            PRODUTOS_SEED,
        )
        conn.commit()

    conn.close()
