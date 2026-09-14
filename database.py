import os
import re
import time
import math
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
    "vendedor_nome": "Voxxel",
    # % que a Voxxel retém sobre o valor de cada pedido atribuído a uma
    # impressora parceira (marketplace) -- editável pelo admin. Pedidos
    # que a própria Voxxel produz (sem impressora parceira) não têm
    # comissão, é 100% dela mesma.
    "comissao_percentual": "15",
    # Precificação do orçamento personalizado. Estes valores são bases
    # editáveis pelo admin e alimentam tanto o cálculo do servidor quanto
    # o preview no navegador.
    "custo_kg_pla": "95",
    "custo_kg_petg": "110",
    "custo_kg_abs": "105",
    "custo_kg_resina": "150",
    "preco_hora_fdm": "4.50",
    "preco_hora_resina": "7.00",
    "reserva_falha_percentual": "10",
    "margem_impressor_percentual": "30",
    "pedido_minimo": "18.90",
}

PRODUTOS_SEED = [
    # nome, categoria, preço, descrição, ângulo do placeholder, estoque, material

    # Catálogo original — preços alinhados à matriz V22
    ("Suporte Geométrico para Plantas", "decoracao", 99.90, "Cachepô facetado para vasos pequenos, produzido sob demanda com acabamento fosco.", "15deg", None, "pla"),
    ("Porta Talheres Poligonal", "decoracao", 109.90, "Organizador de bancada com divisórias e desenho geométrico contemporâneo.", "80deg", None, "pla"),
    ("Escultura Facetada de Mesa", "decoracao", 129.90, "Peça decorativa de linhas facetadas para estantes, nichos e mesas.", "270deg", None, "pla"),
    ("Porta-Caneta Poligonal", "decoracao", 69.90, "Organizador compacto para canetas e pequenos acessórios de escritório.", "190deg", None, "pla"),
    ("Estrutura para Luminária Geométrica", "decoracao", 169.90, "Cúpula decorativa de mesa com desenho vazado; componentes elétricos não inclusos.", "235deg", None, "pla"),
    ("Organizador Modular de Gaveta", "decoracao", 109.90, "Módulos combináveis para organizar utensílios, acessórios e objetos pequenos.", "305deg", None, "pla"),

    ("Máscara Cosplay Cavaleiro", "cosplay", 219.90, "Máscara cenográfica leve, entregue pronta para acabamento e pintura.", "150deg", None, "pla"),
    ("Punho de Manopla Infinity", "cosplay", 299.90, "Acessório cenográfico modular produzido em partes para facilitar a montagem.", "45deg", None, "pla"),
    ("Capacete Modular para Cosplay", "cosplay", 599.90, "Capacete cenográfico dividido em módulos, pronto para acabamento personalizado.", "110deg", None, "pla"),
    ("Ombreira Cenográfica Modular", "cosplay", 329.90, "Par de ombreiras leves com pontos de fixação para compor trajes e armaduras.", "170deg", None, "pla"),
    ("Emblema Personalizado para Traje", "cosplay", 49.90, "Emblema em relevo para roupa, acessório ou exposição, feito sob demanda.", "255deg", None, "pla"),
    ("Suporte Expositor para Máscaras", "cosplay", 139.90, "Base de exposição estável para organizar e destacar máscaras e capacetes.", "335deg", None, "pla"),

    ("Suporte de Celular Articulado", "tecnica", 109.90, "Suporte ajustável de mesa para posicionar o celular em diferentes ângulos.", "220deg", None, "petg"),
    ("Organizador de Ferramentas", "tecnica", 139.90, "Suporte modular para manter ferramentas e acessórios acessíveis na bancada.", "320deg", None, "pla"),
    ("Suporte para Fones", "tecnica", 89.90, "Suporte de bancada para headset com base estável e formato compacto.", "300deg", None, "pla"),
    ("Adaptador para Mangueira e Aspirador", "tecnica", 69.90, "Adaptador funcional sob medida para conectar bocais e mangueiras compatíveis.", "65deg", None, "petg"),
    ("Kit de Presilhas e Guias para Cabos", "tecnica", 59.90, "Conjunto de guias para organizar cabos em mesas, paredes e equipamentos.", "125deg", None, "petg"),
    ("Manopla de Reposição Personalizada", "tecnica", 69.90, "Manopla funcional com encaixe ajustável às medidas informadas no pedido.", "285deg", None, "petg"),

    # Expansão V21/V22 — Decoração & Utilitário
    ("Porta-Chaves Modular de Parede", "decoracao", 89.90, "Sistema compacto de parede com módulos para chaves, chaveiros e pequenos objetos de entrada.", "25deg", None, "pla"),
    ("Bandeja Organizadora Empilhável", "decoracao", 109.90, "Bandeja modular para acessórios, escritório e pequenos objetos, com encaixe para empilhamento.", "55deg", None, "pla"),
    ("Organizador Modular de Maquiagem", "decoracao", 129.90, "Organizador de bancada com divisórias para pincéis, batons, lápis e itens pequenos.", "95deg", None, "pla"),
    ("Porta-Cápsulas de Café Vertical", "decoracao", 149.90, "Organizador vertical para cápsulas de café, pensado para ocupar pouca área de bancada.", "135deg", None, "pla"),
    ("Suporte Ajustável para Livro e Tablet", "decoracao", 149.90, "Suporte de mesa com múltiplas posições de inclinação para livros, tablets e receitas.", "175deg", None, "petg"),
    ("Kit Porta-Copos Geométricos", "decoracao", 99.90, "Conjunto de quatro porta-copos geométricos com base para armazenamento.", "215deg", None, "pla"),
    ("Vaso Autoirrigável Compacto", "decoracao", 119.90, "Vaso decorativo compacto com reservatório separado para reduzir a frequência de rega.", "245deg", None, "petg"),
    ("Porta-Joias Modular com Divisórias", "decoracao", 129.90, "Sistema de bandejas para anéis, brincos, correntes e acessórios, combinável por módulos.", "275deg", None, "pla"),
    ("Organizador de Mesa para Controles Remotos", "decoracao", 89.90, "Base de bancada com espaços separados para controles e pequenos dispositivos domésticos.", "315deg", None, "pla"),
    ("Dispenser Compacto de Sacolas", "decoracao", 139.90, "Organizador vertical para armazenar e retirar sacolas de forma mais organizada.", "345deg", None, "pla"),
    ("Suporte de Parede para Vasos Pequenos", "decoracao", 119.90, "Suporte decorativo para vasos pequenos, pensado para composições de parede.", "35deg", None, "petg"),
    ("Luminária Lithophane Personalizada", "decoracao", 219.90, "Estrutura decorativa para lithophane personalizada a partir de fotografia; componentes elétricos não inclusos.", "75deg", None, "pla"),

    # Expansão V21/V22 — Peça Técnica
    ("Suporte Elevado para Notebook", "tecnica", 119.90, "Base inclinada para elevar notebook, melhorar ergonomia e liberar passagem de ar.", "105deg", None, "petg"),
    ("Suporte VESA para Mini PC", "tecnica", 109.90, "Estrutura para instalar mini PC atrás de monitor compatível ou em superfície vertical.", "145deg", None, "petg"),
    ("Suporte Articulado para Webcam ou Câmera", "tecnica", 89.90, "Suporte compacto com ajuste de ângulo para webcam, câmera leve ou sensor de bancada.", "185deg", None, "petg"),
    ("Caixa Modular para Eletrônica", "tecnica", 99.90, "Gabinete genérico para pequenos projetos eletrônicos, com tampa removível e áreas para adaptação.", "225deg", None, "petg"),
    ("Organizador de Bits e Brocas", "tecnica", 89.90, "Base de bancada com posições para bits, brocas e pequenos acessórios de ferramentas.", "265deg", None, "pla"),
    ("Suporte de Bancada para Multímetro", "tecnica", 89.90, "Base inclinada para manter multímetro visível e estável durante medições.", "305deg", None, "petg"),
    ("Organizador de Pilhas AA e AAA", "tecnica", 99.90, "Dispenser compacto para armazenar pilhas separadas por formato.", "335deg", None, "pla"),
    ("Passa-Cabos de Mesa com Tampa", "tecnica", 49.90, "Acabamento para passagem de cabos em mesa ou bancada, com tampa removível.", "15deg", None, "petg"),
    ("Suporte Sob Mesa para Fonte ou Carregador", "tecnica", 79.90, "Estrutura para fixação inferior de fontes, hubs ou carregadores sob mesa.", "45deg", None, "petg"),
    ("Gabarito de Furação em 90 Graus", "tecnica", 89.90, "Guia para auxiliar furos perpendiculares em trabalhos leves de montagem e marcenaria.", "85deg", None, "petg"),
    ("Suporte de Parede para Roteador ou Modem", "tecnica", 99.90, "Base ventilada para instalação vertical de roteador, modem ou equipamento de rede.", "125deg", None, "petg"),
    ("Suporte para Ferro de Solda e Acessórios", "tecnica", 119.90, "Base para organizar ferro de solda desligado, pontas e acessórios; não substitui suporte térmico durante uso.", "165deg", None, "petg"),

    # Expansão V21/V22 — Cosplay & Acessório
    ("Máscara Oni Estilizada", "cosplay", 229.90, "Máscara cenográfica estilizada produzida para acabamento, pintura e composição de fantasia.", "205deg", None, "pla"),
    ("Máscara Cyberpunk Modular", "cosplay", 269.90, "Máscara cenográfica futurista formada por módulos para facilitar montagem e personalização.", "245deg", None, "pla"),
    ("Colar de Armadura Futurista", "cosplay", 299.90, "Peça cenográfica modular para região do pescoço e ombros, adaptável ao traje.", "285deg", None, "pla"),
    ("Coroa Fantasia Modular", "cosplay", 139.90, "Coroa cenográfica desmontável para fantasia, ensaio ou exposição.", "325deg", None, "pla"),
    ("Tiara Temática com Encaixes", "cosplay", 89.90, "Base de tiara com pontos de encaixe para elementos decorativos intercambiáveis.", "5deg", None, "petg"),
    ("Bracelete Tecnológico Cenográfico", "cosplay", 159.90, "Bracelete de visual sci-fi com peças modulares e espaço para detalhes decorativos.", "45deg", None, "pla"),
    ("Peitoral Modular Cenográfico", "cosplay", 649.90, "Conjunto frontal de armadura leve dividido em módulos para montagem e adaptação ao usuário.", "85deg", None, "pla"),
    ("Caneleira Modular Cenográfica", "cosplay", 499.90, "Peça de armadura leve para perna, segmentada para montagem e ajuste visual.", "125deg", None, "pla"),
    ("Cinto Modular para Cosplay", "cosplay", 379.90, "Sistema de cinto cenográfico com módulos para acessórios e personalização temática.", "165deg", None, "petg"),
    ("Fivela Personalizável para Cinto", "cosplay", 109.90, "Fivela cenográfica com área frontal personalizável para símbolo, nome ou identidade visual.", "205deg", None, "petg"),
    ("Kit de Conectores para Armadura Cosplay", "cosplay", 129.90, "Conjunto de presilhas, conectores e uniões para montagem de armadura cenográfica.", "245deg", None, "petg"),
    ("Chifres Modulares Cenográficos", "cosplay", 189.90, "Par de chifres decorativos desmontáveis com sistema de montagem pensado para transporte.", "285deg", None, "pla"),
]

# Matriz comercial V22 aplicada ao catálogo. A migração abaixo atualiza uma
# única vez o banco já publicado; futuras alterações manuais do admin continuam
# preservadas após a aplicação do marcador catalogo_precos_v22.
PRECOS_CATALOGO_V22 = {produto[0]: produto[2] for produto in PRODUTOS_SEED}

PRODUTOS_ILUSTRACOES = {
    "Suporte Geométrico para Plantas": "suporte-geometrico-plantas.webp",
    "Porta Talheres Poligonal": "porta-talheres-poligonal.webp",
    "Escultura Facetada de Mesa": "escultura-facetada-mesa.webp",
    "Porta-Caneta Poligonal": "porta-caneta-poligonal.webp",
    "Estrutura para Luminária Geométrica": "estrutura-luminaria-geometrica.webp",
    "Organizador Modular de Gaveta": "organizador-modular-gaveta.webp",
    "Máscara Cosplay Cavaleiro": "mascara-cosplay-cavaleiro.webp",
    "Punho de Manopla Infinity": "punho-manopla-cenografica.webp",
    "Capacete Modular para Cosplay": "capacete-modular-cosplay.webp",
    "Ombreira Cenográfica Modular": "ombreira-cenografica-modular.webp",
    "Emblema Personalizado para Traje": "emblema-personalizado-traje.webp",
    "Suporte Expositor para Máscaras": "suporte-expositor-mascaras.webp",
    "Suporte de Celular Articulado": "suporte-celular-articulado.webp",
    "Organizador de Ferramentas": "organizador-ferramentas.webp",
    "Suporte para Fones": "suporte-fones.webp",
    "Adaptador para Mangueira e Aspirador": "adaptador-mangueira-aspirador.webp",
    "Kit de Presilhas e Guias para Cabos": "kit-presilhas-guias-cabos.webp",
    "Manopla de Reposição Personalizada": "manopla-reposicao-personalizada.webp",
}


# Imagens comerciais permanentes da expansão do catálogo (36 novos itens).
# Os arquivos ficam versionados em static/images/products e são servidos pela
# mesma rota /produto/<id>/imagem usada pelas imagens anteriores.
PRODUTOS_ILUSTRACOES.update({
    "Porta-Chaves Modular de Parede": "porta-chaves-modular-de-parede.webp",
    "Bandeja Organizadora Empilhável": "bandeja-organizadora-empilhavel.webp",
    "Organizador Modular de Maquiagem": "organizador-modular-de-maquiagem.webp",
    "Porta-Cápsulas de Café Vertical": "porta-capsulas-de-cafe-vertical.webp",
    "Suporte Ajustável para Livro e Tablet": "suporte-ajustavel-para-livro-e-tablet.webp",
    "Kit Porta-Copos Geométricos": "kit-porta-copos-geometricos.webp",
    "Vaso Autoirrigável Compacto": "vaso-autoirrigavel-compacto.webp",
    "Porta-Joias Modular com Divisórias": "porta-joias-modular-com-divisorias.webp",
    "Organizador de Mesa para Controles Remotos": "organizador-de-mesa-para-controles-remotos.webp",
    "Dispenser Compacto de Sacolas": "dispenser-compacto-de-sacolas.webp",
    "Suporte de Parede para Vasos Pequenos": "suporte-de-parede-para-vasos-pequenos.webp",
    "Luminária Lithophane Personalizada": "luminaria-lithophane-personalizada.webp",
    "Suporte Elevado para Notebook": "suporte-elevado-para-notebook.webp",
    "Suporte VESA para Mini PC": "suporte-vesa-para-mini-pc.webp",
    "Suporte Articulado para Webcam ou Câmera": "suporte-articulado-para-webcam-ou-camera.webp",
    "Caixa Modular para Eletrônica": "caixa-modular-para-eletronica.webp",
    "Organizador de Bits e Brocas": "organizador-de-bits-e-brocas.webp",
    "Suporte de Bancada para Multímetro": "suporte-de-bancada-para-multimetro.webp",
    "Organizador de Pilhas AA e AAA": "organizador-de-pilhas-aa-e-aaa.webp",
    "Passa-Cabos de Mesa com Tampa": "passa-cabos-de-mesa-com-tampa.webp",
    "Suporte Sob Mesa para Fonte ou Carregador": "suporte-sob-mesa-para-fonte-ou-carregador.webp",
    "Gabarito de Furação em 90 Graus": "gabarito-de-furacao-em-90-graus.webp",
    "Suporte de Parede para Roteador ou Modem": "suporte-de-parede-para-roteador-ou-modem.webp",
    "Suporte para Ferro de Solda e Acessórios": "suporte-para-ferro-de-solda-e-acessorios.webp",
    "Máscara Oni Estilizada": "mascara-oni-estilizada.webp",
    "Máscara Cyberpunk Modular": "mascara-cyberpunk-modular.webp",
    "Colar de Armadura Futurista": "colar-de-armadura-futurista.webp",
    "Coroa Fantasia Modular": "coroa-fantasia-modular.webp",
    "Tiara Temática com Encaixes": "tiara-tematica-com-encaixes.webp",
    "Bracelete Tecnológico Cenográfico": "bracelete-tecnologico-cenografico.webp",
    "Peitoral Modular Cenográfico": "peitoral-modular-cenografico.webp",
    "Caneleira Modular Cenográfica": "caneleira-modular-cenografica.webp",
    "Cinto Modular para Cosplay": "cinto-modular-para-cosplay.webp",
    "Fivela Personalizável para Cinto": "fivela-personalizavel-para-cinto.webp",
    "Kit de Conectores para Armadura Cosplay": "kit-de-conectores-para-armadura-cosplay.webp",
    "Chifres Modulares Cenográficos": "chifres-modulares-cenograficos.webp",
})


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

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        # Nunca devolva ao pool uma conexão presa em transação abortada.
        # Um rollback após SELECT/COMMIT é inofensivo e evita contaminar a
        # próxima requisição quando alguma operação SQL falha no meio.
        if USING_POSTGRES:
            try:
                self._conn.rollback()
            except Exception:
                pass
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
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS material TEXT DEFAULT 'pla'")
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS imagem_arquivo TEXT")
        conn.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS imagem_tipo TEXT DEFAULT 'foto_real'")
        conn.execute("UPDATE produtos SET material='pla' WHERE material IS NULL OR material='' ")
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
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS material_requisito TEXT")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS fluxo_status TEXT DEFAULT 'recebido'")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS descricao_projeto TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS requisitos_projeto TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS uso_projeto TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS alteracoes_projeto TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS referencia_status TEXT DEFAULT 'nao_aplicavel'")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS aprovado_cliente INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS producao_autorizada INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cancelado_em TEXT")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS estoque_devolvido INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS repasse_status TEXT DEFAULT 'pendente'")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS repasse_pago_em TEXT")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS repasse_referencia TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_email TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS empresa_nome TEXT DEFAULT ''")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS lead_origem TEXT DEFAULT 'orcamento'")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS acesso_token_hash TEXT")
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
        conn.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS termos_aceitos_em TEXT")
        conn.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS atualizado_em TEXT")
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
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS materiais TEXT DEFAULT 'pla'")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS status_cadastro TEXT DEFAULT 'aprovado'")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS termos_aceitos_em TEXT")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS modelo_impressora TEXT DEFAULT ''")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS tecnologia TEXT DEFAULT 'fdm'")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS volume_x REAL")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS volume_y REAL")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS volume_z REAL")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS capacidade_diaria_horas REAL")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS endereco_base TEXT DEFAULT ''")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS pix_recebimento TEXT DEFAULT ''")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS observacoes_equipamento TEXT DEFAULT ''")
        conn.execute("ALTER TABLE impressoras ADD COLUMN IF NOT EXISTS atualizado_em TEXT")
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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pedido_referencias (
                id SERIAL PRIMARY KEY,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL,
                nome_original TEXT NOT NULL,
                mimetype TEXT,
                dados BYTEA NOT NULL,
                criado_em TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pedido_mensagens (
                id SERIAL PRIMARY KEY,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                autor_tipo TEXT NOT NULL,
                autor_id INTEGER,
                texto TEXT DEFAULT '',
                anexo_nome TEXT,
                anexo_mimetype TEXT,
                anexo_dados BYTEA,
                criado_em TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'),
                lida_cliente INTEGER DEFAULT 0,
                lida_impressora INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_id INTEGER REFERENCES clientes(id)")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS impressora_id INTEGER REFERENCES impressoras(id)")
        conn.commit()

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
            "ALTER TABLE produtos ADD COLUMN material TEXT DEFAULT 'pla'",
            "ALTER TABLE produtos ADD COLUMN imagem_arquivo TEXT",
            "ALTER TABLE produtos ADD COLUMN imagem_tipo TEXT DEFAULT 'foto_real'",
        ):
            try:
                conn.execute(coluna_sql)
            except sqlite3.OperationalError:
                pass
        conn.execute("UPDATE produtos SET material='pla' WHERE material IS NULL OR material='' ")
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
            "ALTER TABLE pedidos ADD COLUMN material_requisito TEXT",
            "ALTER TABLE pedidos ADD COLUMN comissao_voxxel REAL",
            "ALTER TABLE pedidos ADD COLUMN fluxo_status TEXT DEFAULT 'recebido'",
            "ALTER TABLE pedidos ADD COLUMN descricao_projeto TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN requisitos_projeto TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN uso_projeto TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN alteracoes_projeto TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN referencia_status TEXT DEFAULT 'nao_aplicavel'",
            "ALTER TABLE pedidos ADD COLUMN aprovado_cliente INTEGER DEFAULT 0",
            "ALTER TABLE pedidos ADD COLUMN producao_autorizada INTEGER DEFAULT 0",
            "ALTER TABLE pedidos ADD COLUMN cancelado_em TEXT",
            "ALTER TABLE pedidos ADD COLUMN estoque_devolvido INTEGER DEFAULT 0",
            "ALTER TABLE pedidos ADD COLUMN repasse_status TEXT DEFAULT 'pendente'",
            "ALTER TABLE pedidos ADD COLUMN repasse_pago_em TEXT",
            "ALTER TABLE pedidos ADD COLUMN repasse_referencia TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN cliente_email TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN empresa_nome TEXT DEFAULT ''",
            "ALTER TABLE pedidos ADD COLUMN lead_origem TEXT DEFAULT 'orcamento'",
            "ALTER TABLE pedidos ADD COLUMN acesso_token_hash TEXT",
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
        for coluna_sql in (
            "ALTER TABLE clientes ADD COLUMN termos_aceitos_em TEXT",
            "ALTER TABLE clientes ADD COLUMN atualizado_em TEXT",
        ):
            try:
                conn.execute(coluna_sql)
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
        try:
            conn.execute("ALTER TABLE impressoras ADD COLUMN materiais TEXT DEFAULT 'pla'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE impressoras ADD COLUMN status_cadastro TEXT DEFAULT 'aprovado'")
        except sqlite3.OperationalError:
            pass
        for coluna_sql in (
            "ALTER TABLE impressoras ADD COLUMN termos_aceitos_em TEXT",
            "ALTER TABLE impressoras ADD COLUMN modelo_impressora TEXT DEFAULT ''",
            "ALTER TABLE impressoras ADD COLUMN tecnologia TEXT DEFAULT 'fdm'",
            "ALTER TABLE impressoras ADD COLUMN volume_x REAL",
            "ALTER TABLE impressoras ADD COLUMN volume_y REAL",
            "ALTER TABLE impressoras ADD COLUMN volume_z REAL",
            "ALTER TABLE impressoras ADD COLUMN capacidade_diaria_horas REAL",
            "ALTER TABLE impressoras ADD COLUMN endereco_base TEXT DEFAULT ''",
            "ALTER TABLE impressoras ADD COLUMN pix_recebimento TEXT DEFAULT ''",
            "ALTER TABLE impressoras ADD COLUMN observacoes_equipamento TEXT DEFAULT ''",
            "ALTER TABLE impressoras ADD COLUMN atualizado_em TEXT",
        ):
            try:
                conn.execute(coluna_sql)
            except sqlite3.OperationalError:
                pass

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pedido_referencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL,
                nome_original TEXT NOT NULL,
                mimetype TEXT,
                dados BLOB NOT NULL,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pedido_mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                autor_tipo TEXT NOT NULL,
                autor_id INTEGER,
                texto TEXT DEFAULT '',
                anexo_nome TEXT,
                anexo_mimetype TEXT,
                anexo_dados BLOB,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                lida_cliente INTEGER DEFAULT 0,
                lida_impressora INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        try:
            conn.execute("ALTER TABLE pedidos ADD COLUMN impressora_id INTEGER REFERENCES impressoras(id)")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    # Carga incremental do catálogo: inclui lançamentos também nos bancos que
    # já estão em uso, mas preserva produtos existentes e alterações do admin.
    # A comparação pelo nome torna a operação idempotente em SQLite/Postgres.
    for produto in PRODUTOS_SEED:
        conn.execute(
            """INSERT INTO produtos
               (nome, categoria, preco, descricao, imagem_ang, estoque, material)
               SELECT ?, ?, ?, ?, ?, ?, ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM produtos WHERE LOWER(nome) = LOWER(?)
               )""",
            (*produto, produto[0]),
        )

    # Migração única: atualiza o catálogo já publicado para a tabela de preços
    # revisada sem ficar sobrescrevendo futuras alterações feitas pelo admin.
    marcador_preco = conn.execute(
        "SELECT valor FROM configuracoes WHERE chave = ?",
        ("catalogo_precos_v22",),
    ).fetchone()
    if not marcador_preco:
        for nome, preco in PRECOS_CATALOGO_V22.items():
            conn.execute(
                "UPDATE produtos SET preco = ? WHERE LOWER(nome) = LOWER(?)",
                (preco, nome),
            )
        conn.execute(
            "INSERT INTO configuracoes (chave, valor) VALUES (?, ?)",
            ("catalogo_precos_v22", "1"),
        )
        conn.commit()
    # Ilustrações próprias dos itens de demonstração. Fotos reais enviadas
    # pelo admin (BLOB) continuam tendo prioridade e nunca são sobrescritas.
    for nome, arquivo in PRODUTOS_ILUSTRACOES.items():
        conn.execute(
            """UPDATE produtos SET imagem_arquivo=?, imagem_tipo='ilustrativa'
               WHERE LOWER(nome)=LOWER(?) AND imagem_mimetype IS NULL
                 AND (imagem_arquivo IS NULL OR imagem_arquivo='')""",
            (arquivo, nome),
        )
    conn.commit()

    # Snapshot dos itens de pedidos do catálogo. Além de preservar o preço e
    # material no momento da compra, permite reservar estoque de forma
    # transacional sem depender do catálogo mudar depois.
    if USING_POSTGRES:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pedido_itens (
                id SERIAL PRIMARY KEY,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                produto_id INTEGER REFERENCES produtos(id) ON DELETE SET NULL,
                nome TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                preco_unitario REAL NOT NULL,
                subtotal REAL NOT NULL,
                material TEXT DEFAULT 'pla'
            )"""
        )
    else:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pedido_itens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                produto_id INTEGER REFERENCES produtos(id) ON DELETE SET NULL,
                nome TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                preco_unitario REAL NOT NULL,
                subtotal REAL NOT NULL,
                material TEXT DEFAULT 'pla'
            )"""
        )
    conn.commit()

    # Índices pra manter as listagens rápidas conforme o catálogo/pedidos
    # crescem (o filtro por categoria e por status são os mais usados).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_produtos_categoria_ativo ON produtos(categoria, ativo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos(cliente_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_impressora ON pedidos(impressora_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_distribuicao ON pedidos(distribuicao_status, impressora_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_pagamento ON pedidos(status_pagamento, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_impressoras_disponiveis ON impressoras(ativo, online)")
    # Repara eventual duplicidade antiga antes de impor a invariável de fila:
    # no máximo uma oferta pendente por pedido e por parceiro. Isso evita
    # corridas entre polling, painel admin e múltiplos workers HTTP.
    conn.execute("""UPDATE ofertas_impressao SET status='expirada'
                    WHERE status='pendente' AND id NOT IN (
                      SELECT MIN(id) FROM ofertas_impressao WHERE status='pendente' GROUP BY pedido_id
                    )""")
    conn.execute("""UPDATE ofertas_impressao SET status='expirada'
                    WHERE status='pendente' AND id NOT IN (
                      SELECT MIN(id) FROM ofertas_impressao WHERE status='pendente' GROUP BY impressora_id
                    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_pedido ON ofertas_impressao(pedido_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_impressora ON ofertas_impressao(impressora_id, status)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_oferta_pendente_pedido ON ofertas_impressao(pedido_id) WHERE status='pendente'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_oferta_pendente_impressora ON ofertas_impressao(impressora_id) WHERE status='pendente'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_status_criado ON ofertas_impressao(status, criado_em)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedido_itens_pedido ON pedido_itens(pedido_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_referencias_pedido ON pedido_referencias(pedido_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mensagens_pedido ON pedido_mensagens(pedido_id, id)")
    conn.commit()

    # Garante que toda chave de configuração padrão exista (não sobrescreve
    # o que o admin já tiver salvo).
    existentes = {row["chave"] for row in conn.execute("SELECT chave FROM configuracoes").fetchall()}
    faltando = [(chave, valor) for chave, valor in CONFIG_PADRAO.items() if chave not in existentes]
    if faltando:
        conn.executemany("INSERT INTO configuracoes (chave, valor) VALUES (?, ?)", faltando)
        conn.commit()


def criar_pedido(conn, tipo, detalhes, valor_estimado, cliente_nome="", cliente_telefone="",
                  forma_pagamento="combinar", cliente_id=None, cliente_lat=None, cliente_lng=None,
                  material_requisito=None, cliente_email="", empresa_nome="", lead_origem="orcamento",
                  acesso_token_hash=None, commit=True):
    """Insere um pedido (venda da loja ou orçamento) e devolve o id gerado,
    já lidando com a diferença de sintaxe entre SQLite e Postgres.
    `cliente_id` liga o pedido à conta logada -- fica None só para pedidos
    antigos, de antes de existir login (checkout hoje exige conta).
    `cliente_lat`/`cliente_lng` vêm da geolocalização do navegador (podem
    vir None se o cliente não permitiu) -- usados por distribuicao.py pra
    achar a impressora mais próxima."""
    params = (
        tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone, forma_pagamento,
        cliente_id, cliente_lat, cliente_lng, material_requisito, cliente_email,
        empresa_nome, lead_origem, acesso_token_hash,
    )
    if USING_POSTGRES:
        cur = conn.execute(
            """INSERT INTO pedidos (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone,
                                     forma_pagamento, cliente_id, cliente_lat, cliente_lng, material_requisito,
                                     cliente_email, empresa_nome, lead_origem, acesso_token_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            params,
        )
        novo_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            """INSERT INTO pedidos (tipo, detalhes, valor_estimado, cliente_nome, cliente_telefone,
                                     forma_pagamento, cliente_id, cliente_lat, cliente_lng, material_requisito,
                                     cliente_email, empresa_nome, lead_origem, acesso_token_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        novo_id = cur.lastrowid
    if commit:
        conn.commit()
    return novo_id


def ler_coordenada_formulario(valor, minimo=-180.0, maximo=180.0):
    """Converte e limita coordenadas vindas do navegador.

    Latitude deve ser chamada com -90..90; longitude com -180..180. Valores
    fora da faixa são tratados como ausentes para não poluir o roteamento.
    """
    try:
        if valor is None or str(valor).strip() == "":
            return None
        numero = float(valor)
        if not math.isfinite(numero) or numero < minimo or numero > maximo:
            return None
        return numero
    except (TypeError, ValueError):
        return None


# ---------- impressoras parceiras (marketplace) ----------

def criar_impressora(conn, nome, telefone, senha_hash, materiais="pla"):
    """Cria um parceiro em estado pendente; só o admin pode liberar a rede."""
    telefone = normalizar_telefone(telefone)
    materiais = materiais or "pla"
    if USING_POSTGRES:
        cur = conn.execute(
            "INSERT INTO impressoras (nome, telefone, senha_hash, materiais, ativo, status_cadastro) VALUES (?, ?, ?, ?, 0, 'pendente') RETURNING id",
            (nome, telefone, senha_hash, materiais),
        )
        novo_id = cur.fetchone()["id"]
    else:
        cur = conn.execute(
            "INSERT INTO impressoras (nome, telefone, senha_hash, materiais, ativo, status_cadastro) VALUES (?, ?, ?, ?, 0, 'pendente')",
            (nome, telefone, senha_hash, materiais),
        )
        novo_id = cur.lastrowid
    conn.commit()
    return novo_id


def atualizar_materiais_impressora(conn, impressora_id, materiais):
    """Atualiza a lista CSV de materiais que a parceira realmente consegue produzir."""
    conn.execute("UPDATE impressoras SET materiais = ? WHERE id = ?", (materiais or "pla", impressora_id))
    conn.commit()


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
    """Aprova/pausa um parceiro e o remove da fila enquanto estiver inativo."""
    status = "aprovado" if ativo else "bloqueado"
    conn.execute(
        "UPDATE impressoras SET ativo = ?, online = 0, status_cadastro = ? WHERE id = ?",
        (1 if ativo else 0, status, impressora_id),
    )
    conn.commit()


def listar_pedidos_da_impressora(conn, impressora_id):
    return conn.execute(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM pedido_mensagens m WHERE m.pedido_id=p.id AND m.lida_impressora=0 AND m.autor_tipo!='impressora') AS mensagens_nao_lidas
           FROM pedidos p WHERE p.impressora_id = ? ORDER BY p.id DESC""", (impressora_id,)
    ).fetchall()


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
        """SELECT p.*, i.nome AS impressora_nome,
                  (SELECT COUNT(*) FROM pedido_mensagens m WHERE m.pedido_id=p.id AND m.lida_cliente=0 AND m.autor_tipo!='cliente') AS mensagens_nao_lidas
           FROM pedidos p
           LEFT JOIN impressoras i ON i.id = p.impressora_id
           WHERE p.cliente_id = ?
           ORDER BY p.id DESC""",
        (cliente_id,),
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
        if not math.isfinite(pct):
            raise ValueError
    except (TypeError, ValueError):
        pct = 15.0
    return max(0.0, min(pct, 80.0))


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
        "SELECT COALESCE(SUM(comissao_voxxel), 0) AS total FROM pedidos WHERE comissao_voxxel IS NOT NULL AND status_pagamento='confirmado' AND status<>'cancelado' "
    ).fetchone()["total"]
    por_impressora = conn.execute(
        """SELECT i.id, i.nome, COUNT(p.id) AS pedidos,
                  COALESCE(SUM(p.comissao_voxxel), 0) AS comissao_total,
                  COALESCE(SUM(p.valor_estimado), 0) AS faturamento_total
           FROM impressoras i
           JOIN pedidos p ON p.impressora_id = i.id AND p.comissao_voxxel IS NOT NULL AND p.status_pagamento='confirmado' AND p.status<>'cancelado' 
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
