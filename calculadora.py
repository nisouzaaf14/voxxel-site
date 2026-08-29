# Mesma lógica da calculadora do site, em Python, para que o valor
# calculado no navegador (preview, em orcamento.html) e o valor salvo no
# pedido sejam sempre iguais.
#
# O BUG ENCONTRADO: o JavaScript do preview (orcamento.html) já lia
# `mat.preco_kg` pra cada material -- mas esse campo nunca existiu no
# MATERIAIS do Python (só existiam "nome" e "densidade"). Ou seja, o
# preview no navegador calculava `custoMaterial = peso * undefined`,
# virava `NaN`, e contaminava o preço total mostrado ao cliente ANTES de
# enviar o orçamento. Além disso o preview usava uma hora de máquina fixa
# de R$9 (HORA_MAQUINA no JS) enquanto o servidor sempre cobrou R$2 --
# ou seja, mesmo se preco_kg existisse, o valor mostrado na tela nunca
# bateria com o valor realmente salvo no pedido.
#
# Correção: cada material agora tem seu "preco_kg" de verdade (o nome que
# o front-end já esperava), e as constantes de preço abaixo
# (PRECO_HORA_IMPRESSAO, SHELL_FRACTION, CAT_ACABAMENTO) são enviadas para
# o template via app.py e usadas pelo JS -- uma fonte única de verdade,
# em vez de dois lugares que podem ficar dessincronizados de novo.
#
# Regra de preço (definida pela Voxxel):
#   - R$ 2,00 por hora de impressão (custo de máquina/energia, igual pra
#     qualquer material)
#   - X reais por kg de material gasto, onde X depende do material
#     escolhido -- resina e PETG custam mais caro por grama que o PLA.
# O preço final de cada peça é a soma desses dois valores + o acabamento
# (post-processamento manual, que varia por categoria).
#
# Os valores de "preco_kg" abaixo são um ponto de partida realista pro
# mercado brasileiro em 2026 -- ajuste pelo custo real que a Voxxel paga
# no rolo/galão (preço do rolo ÷ peso do rolo em kg) sempre que o
# fornecedor mudar de preço.

MATERIAIS = {
    "pla":    {"nome": "PLA",    "densidade": 1.24, "preco_kg": 180},
    "petg":   {"nome": "PETG",   "densidade": 1.27, "preco_kg": 210},
    "abs":    {"nome": "ABS",    "densidade": 1.04, "preco_kg": 200},
    "resina": {"nome": "Resina", "densidade": 1.10, "preco_kg": 360},
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
PRECO_HORA_IMPRESSAO = 2.00   # R$ por hora de impressão (igual pra todo material)
# O preço por kg de material vem de MATERIAIS[material]["preco_kg"] --
# cada material tem o seu (ver comentário acima).

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
    # custo pelo material gasto -- preço por kg específico do material
    # escolhido, não mais um valor único pra todos
    custo_material = (peso_gramas / 1000) * mat["preco_kg"]

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