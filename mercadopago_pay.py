# Pagamento por cartão de crédito/débito via Mercado Pago (Checkout Pro).
#
# Por que Mercado Pago e não um formulário de cartão feito à mão?
# Processar número de cartão diretamente no servidor da Voxxel exigiria
# certificação PCI-DSS (segurança de dados de cartão) — inviável para uma
# loja pequena. O Checkout Pro resolve isso: o cliente é redirecionado pra
# uma página segura do próprio Mercado Pago, digita os dados do cartão lá
# (nunca chegam no nosso servidor), e a gente só recebe a confirmação.
#
# O admin precisa cadastrar o "Access Token" da conta Mercado Pago da loja
# em Painel > Configurações. É gratuito criar a conta; o token de produção
# fica em https://www.mercadopago.com.br/developers/panel/app.

import mercadopago
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _cliente(access_token):
    return mercadopago.SDK(access_token)


def criar_preferencia(access_token, pedido_id, descricao, valor, url_base):
    """Cria uma "preferência" de pagamento (o carrinho, em termos do
    Mercado Pago) e devolve (preference_id, init_point) — o init_point é o
    link da página segura de pagamento pra onde o cliente é redirecionado."""
    sdk = _cliente(access_token)
    preferencia = {
        "items": [{
            "title": descricao[:250] or f"Pedido Voxxel #{pedido_id}",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": float(valor),
        }],
        "external_reference": str(pedido_id),
        "back_urls": {
            "success": f"{url_base}/pedido/{pedido_id}/retorno-cartao?status=approved",
            "pending": f"{url_base}/pedido/{pedido_id}/retorno-cartao?status=pending",
            "failure": f"{url_base}/pedido/{pedido_id}/retorno-cartao?status=failure",
        },
        "auto_return": "approved",
        "notification_url": f"{url_base}/webhooks/mercadopago",
    }
    resposta = sdk.preference().create(preferencia)
    corpo = resposta.get("response", {})
    return corpo.get("id"), corpo.get("init_point")


def consultar_pagamento(access_token, payment_id):
    """Consulta o status de um pagamento específico direto na API do
    Mercado Pago (nunca confiamos só no retorno da URL, que o cliente
    poderia manipular)."""
    sdk = _cliente(access_token)
    resposta = sdk.payment().get(payment_id)
    return resposta.get("response", {})


def buscar_pagamentos_por_pedido(access_token, pedido_id):
    """Usado pelo webhook: dado o external_reference (id do pedido),
    procura pagamentos associados a ele."""
    sdk = _cliente(access_token)
    filtros = {"external_reference": str(pedido_id)}
    resposta = sdk.payment().search(filtros)
    return resposta.get("response", {}).get("results", [])


def pagamento_confere_com_pedido(dados, pedido_id, valor_esperado):
    """Valida os campos que vinculam um pagamento aprovado ao pedido.

    Nunca basta receber `status=approved`: conferimos referência externa,
    moeda e valor consultando a API do Mercado Pago com o token da Voxxel.
    """
    if not isinstance(dados, dict) or dados.get("status") != "approved":
        return False
    if str(dados.get("external_reference") or "") != str(pedido_id):
        return False
    if (dados.get("currency_id") or "BRL") != "BRL":
        return False
    try:
        cent = Decimal("0.01")
        recebido = Decimal(str(dados.get("transaction_amount"))).quantize(cent, rounding=ROUND_HALF_UP)
        esperado = Decimal(str(valor_esperado)).quantize(cent, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return recebido == esperado
