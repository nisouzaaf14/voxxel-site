# Mesma lógica da calculadora do site, em Python, para que o valor
# calculado no navegador (preview) e o valor salvo no pedido sejam sempre iguais.
#
# Regra de preço (definida pela Voxxel):
#   - R$ 1,70 por hora de impressão
#   - R$ 1,50 a cada 10 gramas de material gasto
# O preço final de cada peça é a soma desses dois valores + o acabamento
# (post-processamento manual, que varia por categoria).

MATERIAIS = {
    "pla":    {"nome": "PLA",    "densidade": 1.24},
    "petg":   {"nome": "PETG",   "densidade": 1.27},
    "abs":    {"nome": "ABS",    "densidade": 1.04},
    "resina": {"nome": "Resina", "densidade": 1.10},
}

# velocidade em cm3/hora e multiplicador de acabamento.
# quanto mais detalhada a impressão, mais fina a camada -> mais devagar e mais cara.
QUALIDADE = {
    "rascunho":  {"velocidade": 38, "mult": 0.85},
    "padrao":    {"velocidade": 22, "mult": 1.0},
    "detalhado": {"velocidade": 11, "mult": 1.35},
}

COMPLEXIDADE = {
    "baixa": {"infill": 0.10, "tempo_mult": 1.0},
    "media": {"infill": 0.20, "tempo_mult": 1.2},
    "alta":  {"infill": 0.35, "tempo_mult": 1.45},
}

SHELL_FRACTION = 0.15

# --- regra de preço ---
PRECO_HORA_IMPRESSAO = 2.00   # R$ por hora de impressão
PRECO_POR_10G = 2.00          # R$ a cada 10 gramas de material (filamento)

CAT_ACABAMENTO = {"tecnica": 6, "cosplay": 14, "decoracao": 8}
CAT_NOME = {"tecnica": "Peça Técnica", "cosplay": "Cosplay & Acessório", "decoracao": "Decoração & Utilitário"}


def calcular_orcamento(altura, largura, profundidade, quantidade, categoria, complexidade, material, qualidade):
    mat = MATERIAIS[material]
    qual = QUALIDADE[qualidade]
    comp = COMPLEXIDADE[complexidade]
    qtd = max(1, int(quantidade))

    volume_caixa = altura * largura * profundidade
    fracao_solida = SHELL_FRACTION + comp["infill"] * (1 - SHELL_FRACTION)
    volume_impresso = volume_caixa * fracao_solida

    peso_gramas = volume_impresso * mat["densidade"]

    horas_impressao = (volume_impresso / qual["velocidade"]) * comp["tempo_mult"]

    # custo pela hora de máquina/impressão
    custo_maquina = horas_impressao * PRECO_HORA_IMPRESSAO
    # custo pelo material gasto (a cada 10g)
    custo_material = (peso_gramas / 10) * PRECO_POR_10G

    custo_acabamento = CAT_ACABAMENTO[categoria] * qual["mult"]

    preco_unitario = custo_material + custo_maquina + custo_acabamento
    preco_total = preco_unitario * qtd

    return {
        "preco_total": round(preco_total, 2),
        "horas_total": round(horas_impressao * qtd, 2),
        "peso_total_g": round(peso_gramas * qtd, 1),
        "custo_material": round(custo_material * qtd, 2),
        "custo_maquina": round(custo_maquina * qtd, 2),
        "custo_acabamento": round(custo_acabamento * qtd, 2),
        "material_nome": mat["nome"],
        "categoria_nome": CAT_NOME[categoria],
    }


def formatar_horas(h):
    if h < 1:
        return f"{round(h * 60)} min"
    horas = int(h)
    minutos = round((h - horas) * 60)
    return f"{horas}h" + (f" {minutos}min" if minutos > 0 else "")