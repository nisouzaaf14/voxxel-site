# Lógica de despacho de pedidos para as impressoras parceiras (o "iFood de
# impressão 3D"). Ideia geral:
#
#   1. Quando um pedido é criado (loja ou orçamento) e o cliente compartilhou
#      localização, ele entra numa fila de despacho.
#   2. A gente acha a impressora ONLINE e ATIVA mais próxima do cliente que
#      ainda não recusou (nem já está com uma oferta pendente) esse pedido,
#      e cria uma "oferta" pendente pra ela.
#   3. A impressora vê a oferta no painel dela e Aceita ou Recusa.
#      - Aceitou: o pedido fica atribuído a ela.
#      - Recusou (ou não respondeu a tempo): a oferta expira e a gente
#        oferece pra próxima mais próxima, e assim por diante.
#   4. Se não sobrar nenhuma impressora disponível, o pedido fica marcado
#      como "sem impressora" pra Voxxel decidir manualmente pelo painel.
#
# Como o site roda num único processo Flask sem fila/worker em segundo
# plano (Celery, Redis etc. seriam overkill pra esse tamanho de loja), o
# avanço da fila é "preguiçoso": a cada vez que alguém olha uma tela que
# depende disso (painel da impressora, painel do admin, página de
# pagamento do pedido) a gente checa se a oferta atual expirou e, se sim,
# já passa pra próxima -- sem precisar de nenhum processo rodando sozinho.

import math
import time

from database import aplicar_comissao_pedido

TIMEOUT_OFERTA_SEGUNDOS = 5 * 60  # 5 minutos pra impressora aceitar/recusar
LOCALIZACAO_VALIDADE_SEGUNDOS = 3 * 60  # impressora some da fila se parar de enviar heartbeat

STATUS_SEM_LOCALIZACAO = "sem_localizacao"
STATUS_BUSCANDO = "buscando"
STATUS_ATRIBUIDO = "atribuido"
STATUS_SEM_IMPRESSORA = "sem_impressora"


def haversine_km(lat1, lon1, lat2, lon2):
    """Distância em linha reta (km) entre duas coordenadas -- fórmula
    padrão de Haversine. Não é a distância real de rota, mas é suficiente
    pra ordenar "quem está mais perto"."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _agora():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _segundos_desde(timestamp_str):
    try:
        estrutura = time.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        # Timestamp inválido nunca deve transformar uma localização/oferta
        # antiga em "recente". Trate como infinitamente velha.
        return float("inf")
    return max(0.0, time.time() - time.mktime(estrutura))


def localizacao_recente(impressora):
    if not impressora or not impressora["localizacao_em"]:
        return False
    return _segundos_desde(impressora["localizacao_em"]) <= LOCALIZACAO_VALIDADE_SEGUNDOS


def avancar_filas_pendentes(conn, limite=100):
    """Expira ofertas vencidas de toda a rede de forma oportunista.

    Como o MVP não usa worker/Redis, qualquer parceiro online que esteja
    consultando ofertas ajuda a fila global a continuar andando.
    """
    rows = conn.execute(
        "SELECT DISTINCT pedido_id FROM ofertas_impressao WHERE status='pendente' ORDER BY pedido_id LIMIT ?",
        (int(limite),),
    ).fetchall()
    for row in rows:
        avancar_distribuicao(conn, row["pedido_id"])


def despachar_pedido(conn, pedido_id):
    """Chamado uma vez, logo depois que um pedido é criado. Decide se dá
    pra tentar achar uma impressora (precisa da localização do cliente) e
    já cria a primeira oferta."""
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        return
    if pedido["cliente_lat"] is None or pedido["cliente_lng"] is None:
        conn.execute(
            "UPDATE pedidos SET distribuicao_status = ? WHERE id = ?",
            (STATUS_SEM_LOCALIZACAO, pedido_id),
        )
        conn.commit()
        return
    conn.execute(
        "UPDATE pedidos SET distribuicao_status = ? WHERE id = ?",
        (STATUS_BUSCANDO, pedido_id),
    )
    conn.commit()
    avancar_distribuicao(conn, pedido_id)


def _candidatos_disponiveis(conn, pedido_id, cliente_lat, cliente_lng):
    """Impressoras online, ativas, com localização conhecida e compatíveis
    com o material exigido pelo pedido. Dentro desse conjunto, remove quem
    já recusou/expirou e ordena por proximidade do cliente."""
    ja_ofertadas = {
        row["impressora_id"]
        for row in conn.execute(
            "SELECT impressora_id FROM ofertas_impressao WHERE pedido_id = ? AND status IN ('recusada','expirada')",
            (pedido_id,),
        ).fetchall()
    }
    pedido = conn.execute(
        "SELECT material_requisito FROM pedidos WHERE id = ?", (pedido_id,)
    ).fetchone()
    material_requisito = pedido["material_requisito"] if pedido else None
    materiais_requeridos = {
        m.strip().lower() for m in (material_requisito or "").split(",") if m.strip()
    }

    impressoras = conn.execute(
        """SELECT id, nome, latitude, longitude, localizacao_em, materiais FROM impressoras
           WHERE online = 1 AND ativo = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL"""
    ).fetchall()

    # Uma parceira não deve receber duas "corridas" simultâneas. Ofertas
    # antigas que já ultrapassaram o timeout não a bloqueiam aqui; serão
    # expiradas pelo avanço normal da fila.
    ocupadas = set()
    for pendente in conn.execute(
        "SELECT impressora_id, criado_em FROM ofertas_impressao WHERE status = 'pendente'"
    ).fetchall():
        if _segundos_desde(pendente["criado_em"]) < TIMEOUT_OFERTA_SEGUNDOS:
            ocupadas.add(pendente["impressora_id"])

    candidatos = []
    for imp in impressoras:
        if imp["id"] in ja_ofertadas or imp["id"] in ocupadas:
            continue
        if not localizacao_recente(imp):
            continue
        materiais_suportados = {m.strip().lower() for m in (imp["materiais"] or "pla").split(",") if m.strip()}
        if materiais_requeridos and not materiais_requeridos.issubset(materiais_suportados):
            continue
        distancia = haversine_km(cliente_lat, cliente_lng, imp["latitude"], imp["longitude"])
        candidatos.append((distancia, imp))
    candidatos.sort(key=lambda par: par[0])
    return candidatos


def avancar_distribuicao(conn, pedido_id):
    """Garante que o estado de despacho do pedido está em dia: expira
    oferta pendente vencida, e se não houver nenhuma oferta pendente,
    tenta criar uma nova pra próxima impressora mais próxima disponível.
    Seguro de chamar repetidamente (é exatamente pra isso que existe)."""
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido or pedido["impressora_id"] is not None:
        return  # já foi aceito por alguém, nada a fazer
    if pedido["cliente_lat"] is None or pedido["cliente_lng"] is None:
        return  # nunca teve como despachar esse pedido

    oferta_pendente = conn.execute(
        "SELECT * FROM ofertas_impressao WHERE pedido_id = ? AND status = 'pendente' ORDER BY id DESC LIMIT 1",
        (pedido_id,),
    ).fetchone()

    if oferta_pendente:
        # Não segura a fila por cinco minutos se a parceira saiu do ar ou
        # deixou a localização vencer. Em uso real isso evita pedidos
        # "presos" quando o navegador do parceiro fecha no meio da oferta.
        parceira_ofertada = conn.execute(
            "SELECT ativo, online, localizacao_em FROM impressoras WHERE id = ?",
            (oferta_pendente["impressora_id"],),
        ).fetchone()
        oferta_ainda_valida = (
            parceira_ofertada
            and parceira_ofertada["ativo"]
            and parceira_ofertada["online"]
            and localizacao_recente(parceira_ofertada)
            and _segundos_desde(oferta_pendente["criado_em"]) < TIMEOUT_OFERTA_SEGUNDOS
        )
        if oferta_ainda_valida:
            return  # ainda dentro do prazo e parceira realmente disponível
        conn.execute(
            "UPDATE ofertas_impressao SET status = 'expirada', respondido_em = ? WHERE id = ? AND status = 'pendente'",
            (_agora(), oferta_pendente["id"]),
        )
        conn.commit()

    candidatos = _candidatos_disponiveis(conn, pedido_id, pedido["cliente_lat"], pedido["cliente_lng"])
    if not candidatos:
        conn.execute(
            "UPDATE pedidos SET distribuicao_status = ? WHERE id = ?",
            (STATUS_SEM_IMPRESSORA, pedido_id),
        )
        conn.commit()
        return

    _distancia, proxima = candidatos[0]
    conn.execute(
        """INSERT INTO ofertas_impressao (pedido_id, impressora_id, status, criado_em)
           VALUES (?, ?, 'pendente', ?)""",
        (pedido_id, proxima["id"], _agora()),
    )
    conn.execute(
        "UPDATE pedidos SET distribuicao_status = ? WHERE id = ?",
        (STATUS_BUSCANDO, pedido_id),
    )
    conn.commit()


def segundos_restantes_oferta(oferta):
    """Quanto tempo ainda falta (em segundos) pra oferta expirar -- usado
    só pra mostrar o cronômetro no painel da impressora."""
    return max(0, int(TIMEOUT_OFERTA_SEGUNDOS - _segundos_desde(oferta["criado_em"])))


def oferta_pendente_da_impressora(conn, impressora_id):
    """Oferta pendente e ainda dentro do prazo pra essa impressora (ou
    None). Já garante que ofertas vencidas de OUTROS pedidos dessa
    impressora sejam avançadas antes de responder, pra não mostrar uma
    oferta "fantasma" que na real já expirou."""
    impressora = conn.execute(
        "SELECT ativo FROM impressoras WHERE id = ?", (impressora_id,)
    ).fetchone()
    if not impressora or not impressora["ativo"]:
        return None  # impressora bloqueada pelo admin não recebe/responde ofertas
    ofertas = conn.execute(
        """SELECT * FROM ofertas_impressao
           WHERE impressora_id = ? AND status = 'pendente' ORDER BY id ASC""",
        (impressora_id,),
    ).fetchall()
    for oferta in ofertas:
        if _segundos_desde(oferta["criado_em"]) >= TIMEOUT_OFERTA_SEGUNDOS:
            avancar_distribuicao(conn, oferta["pedido_id"])
            continue
        return oferta
    return None


def responder_oferta(conn, oferta_id, impressora_id, aceitar):
    """Aceita/recusa de forma atômica o máximo possível sem worker externo."""
    oferta = conn.execute(
        "SELECT * FROM ofertas_impressao WHERE id = ? AND impressora_id = ?",
        (oferta_id, impressora_id),
    ).fetchone()
    if not oferta or oferta["status"] != "pendente":
        return False
    impressora = conn.execute(
        "SELECT ativo, online, localizacao_em FROM impressoras WHERE id = ?", (impressora_id,)
    ).fetchone()
    if (not impressora or not impressora["ativo"] or not impressora["online"]
            or not localizacao_recente(impressora)):
        # A oferta não pode ser aceita por uma parceira que deixou de estar
        # realmente disponível durante o cronômetro.
        avancar_distribuicao(conn, oferta["pedido_id"])
        return False
    if _segundos_desde(oferta["criado_em"]) >= TIMEOUT_OFERTA_SEGUNDOS:
        avancar_distribuicao(conn, oferta["pedido_id"])
        return False

    if aceitar:
        cur = conn.execute(
            "UPDATE ofertas_impressao SET status='aceita', respondido_em=? WHERE id=? AND status='pendente'",
            (_agora(), oferta_id),
        )
        if cur.rowcount != 1:
            conn.rollback(); return False
        pedido = conn.execute("SELECT tipo, valor_estimado FROM pedidos WHERE id=?", (oferta["pedido_id"],)).fetchone()
        if not pedido:
            conn.rollback(); return False
        fluxo = "producao_autorizada" if pedido["tipo"] == "loja" else "em_analise"
        aprovado = 1 if pedido["tipo"] == "loja" else 0
        autorizado = aprovado
        cur = conn.execute(
            """UPDATE pedidos SET impressora_id=?, distribuicao_status=?, fluxo_status=?,
               aprovado_cliente=?, producao_autorizada=? WHERE id=? AND impressora_id IS NULL""",
            (impressora_id, STATUS_ATRIBUIDO, fluxo, aprovado, autorizado, oferta["pedido_id"]),
        )
        if cur.rowcount != 1:
            conn.rollback(); return False
        conn.execute(
            "UPDATE ofertas_impressao SET status='expirada', respondido_em=? WHERE pedido_id=? AND id<>? AND status='pendente'",
            (_agora(), oferta["pedido_id"], oferta_id),
        )
        aplicar_comissao_pedido(conn, oferta["pedido_id"], pedido["valor_estimado"])
        conn.commit()
    else:
        cur = conn.execute(
            "UPDATE ofertas_impressao SET status='recusada', respondido_em=? WHERE id=? AND status='pendente'",
            (_agora(), oferta_id),
        )
        if cur.rowcount != 1:
            conn.rollback(); return False
        conn.commit()
        avancar_distribuicao(conn, oferta["pedido_id"])
    return True

def reconsiderar_pedidos_sem_impressora(conn):
    """Pedidos que ficaram sem nenhuma impressora disponível não tentam de
    novo sozinhos (não tem worker rodando em segundo plano) -- por isso
    isso é chamado sempre que uma nova impressora fica online e também
    sempre que o admin abre a lista de pedidos, pra dar uma segunda chance
    a esses pedidos assim que aparecer alguém disponível."""
    pendentes = conn.execute(
        "SELECT id FROM pedidos WHERE distribuicao_status = ? AND impressora_id IS NULL",
        (STATUS_SEM_IMPRESSORA,),
    ).fetchall()
    for row in pendentes:
        avancar_distribuicao(conn, row["id"])


def atribuir_manualmente(conn, pedido_id, impressora_id):
    """Usado pelo painel do admin como válvula de escape: atribui um
    pedido direto a uma impressora especificada, sem passar pela fila de
    ofertas (útil quando ninguém aceitou automaticamente)."""
    conn.execute(
        """INSERT INTO ofertas_impressao (pedido_id, impressora_id, status, criado_em, respondido_em)
           VALUES (?, ?, 'aceita', ?, ?)""",
        (pedido_id, impressora_id, _agora(), _agora()),
    )
    pedido = conn.execute("SELECT tipo, valor_estimado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    fluxo = "producao_autorizada" if pedido and pedido["tipo"] == "loja" else "em_analise"
    aprovado = 1 if pedido and pedido["tipo"] == "loja" else 0
    conn.execute(
        "UPDATE pedidos SET impressora_id=?, distribuicao_status=?, fluxo_status=?, aprovado_cliente=?, producao_autorizada=? WHERE id=?",
        (impressora_id, STATUS_ATRIBUIDO, fluxo, aprovado, aprovado, pedido_id),
    )
    # Mesma regra de comissão da aceitação automática -- atribuição manual
    # também é um pedido indo pra rede de parceiros, não muda o negócio.
    if pedido:
        aplicar_comissao_pedido(conn, pedido_id, pedido["valor_estimado"])
    conn.commit()
