# Mesma lógica da calculadora do site, em Python, para que o valor
# calculado no navegador (preview) e o valor salvo no pedido sejam sempre iguais.

MATERIAIS = {
    "pla":    {"nome": "PLA",    "densidade": 1.24, "preco_kg": 90},
    "petg":   {"nome": "PETG",   "densidade": 1.27, "preco_kg": 110},
    "abs":    {"nome": "ABS",    "densidade": 1.04, "preco_kg": 100},
    "resina": {"nome": "Resina", "densidade": 1.10, "preco_kg": 250},
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
HORA_MAQUINA = 9.0
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
    custo_material = (peso_gramas / 1000) * mat["preco_kg"]

    horas_impressao = (volume_impresso / qual["velocidade"]) * comp["tempo_mult"]
    custo_maquina = horas_impressao * HORA_MAQUINA

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
