"""Motor de simulação de orçamento da Voxxel.

A regra procura equilibrar quatro coisas:
1) custo real de material (com uma reserva para perdas),
2) tempo de ocupação/desgaste da impressora,
3) preparação/acabamento manual,
4) lucro do parceiro, preservado mesmo depois da comissão da Voxxel.

A simulação continua sendo uma estimativa: geometria, suportes, orientação,
modelo real, disponibilidade e acabamento ainda precisam ser confirmados.
"""

MATERIAIS = {
    "pla": {
        "nome": "PLA", "densidade": 1.24, "processo": "FDM", "selo": "Mais comum",
        "descricao": "Versátil para protótipos, decoração e várias peças do dia a dia.",
    },
    "petg": {
        "nome": "PETG", "densidade": 1.27, "processo": "FDM", "selo": "Uso funcional",
        "descricao": "Boa opção quando o projeto pede mais resistência e durabilidade.",
    },
    "abs": {
        "nome": "ABS", "densidade": 1.04, "processo": "FDM", "selo": "Sob consulta",
        "descricao": "Indicado para aplicações específicas; exige impressora e ambiente compatíveis.",
    },
    "resina": {
        "nome": "Resina", "densidade": 1.10, "processo": "SLA/MSLA", "selo": "Alto detalhe",
        "descricao": "Voltada a peças pequenas e detalhadas, quando a rede possui equipamento compatível.",
    },
}

QUALIDADE = {
    "rascunho":  {"velocidade": 38, "mult": 0.85},
    "padrao":    {"velocidade": 22, "mult": 1.0},
    "detalhado": {"velocidade": 11, "mult": 1.35},
}

COMPLEXIDADE = {
    "baixa": {"infill": 0.10, "tempo_mult": 1.0, "risco_mult": 0.90},
    "media": {"infill": 0.20, "tempo_mult": 1.2, "risco_mult": 1.00},
    "alta":  {"infill": 0.35, "tempo_mult": 1.45, "risco_mult": 1.25},
}

SHELL_FRACTION = 0.15
CAT_ACABAMENTO = {"tecnica": 4.0, "cosplay": 8.0, "decoracao": 5.0}
CAT_PREPARACAO = {"tecnica": 5.0, "cosplay": 8.0, "decoracao": 6.0}
CAT_NOME = {"tecnica": "Peça Técnica", "cosplay": "Cosplay & Acessório", "decoracao": "Decoração & Utilitário"}

# Defaults seguros para a simulação. Todos podem ser alterados no admin.
PRECIFICACAO_PADRAO = {
    "custo_kg": {"pla": 95.0, "petg": 110.0, "abs": 105.0, "resina": 150.0},
    "hora_fdm": 4.50,
    "hora_resina": 7.00,
    "reserva_falha_pct": 10.0,
    "margem_impressor_pct": 30.0,
    "comissao_voxxel_pct": 15.0,
    "pedido_minimo": 18.90,
}


def _float_config(config, chave, padrao, minimo=0.0, maximo=None):
    try:
        valor = float(str(config.get(chave, padrao)).replace(",", ".")) if config else float(padrao)
    except (TypeError, ValueError):
        valor = float(padrao)
    valor = max(minimo, valor)
    if maximo is not None:
        valor = min(maximo, valor)
    return valor


def regras_precificacao(config=None):
    """Converte configurações do admin para a estrutura usada pelo cálculo."""
    return {
        "custo_kg": {
            "pla": _float_config(config, "custo_kg_pla", 95),
            "petg": _float_config(config, "custo_kg_petg", 110),
            "abs": _float_config(config, "custo_kg_abs", 105),
            "resina": _float_config(config, "custo_kg_resina", 150),
        },
        "hora_fdm": _float_config(config, "preco_hora_fdm", 4.50),
        "hora_resina": _float_config(config, "preco_hora_resina", 7.00),
        "reserva_falha_pct": _float_config(config, "reserva_falha_percentual", 10, 0, 60),
        "margem_impressor_pct": _float_config(config, "margem_impressor_percentual", 30, 0, 200),
        "comissao_voxxel_pct": _float_config(config, "comissao_percentual", 15, 0, 80),
        "pedido_minimo": _float_config(config, "pedido_minimo", 18.90),
    }


def materiais_com_preco(regras):
    """Cópia dos materiais com custo/kg injetado para o preview no navegador."""
    saida = {}
    for chave, dados in MATERIAIS.items():
        saida[chave] = dict(dados)
        saida[chave]["preco_kg"] = regras["custo_kg"][chave]
    return saida


def calcular_orcamento(altura, largura, profundidade, quantidade, categoria, complexidade, material, qualidade, regras=None):
    regras = regras or PRECIFICACAO_PADRAO
    mat = MATERIAIS[material]
    qual = QUALIDADE[qualidade]
    comp = COMPLEXIDADE[complexidade]
    qtd = max(1, int(quantidade))

    volume_caixa = max(0.0, altura) * max(0.0, largura) * max(0.0, profundidade)
    fracao_solida = SHELL_FRACTION + comp["infill"] * (1 - SHELL_FRACTION)
    volume_impresso = volume_caixa * fracao_solida

    peso_unit_g = volume_impresso * mat["densidade"]
    horas_unit = (volume_impresso / qual["velocidade"]) * comp["tempo_mult"] if volume_impresso > 0 else 0

    # Custo direto de insumo. A reserva cobre brim/raft, purga, suportes,
    # pequenas perdas e uma parcela estatística de reimpressões.
    custo_material_base_unit = (peso_unit_g / 1000) * regras["custo_kg"][material]
    reserva_pct = (regras["reserva_falha_pct"] / 100) * comp.get("risco_mult", 1.0)
    custo_material_unit = custo_material_base_unit * (1 + reserva_pct)

    hora_maquina = regras["hora_resina"] if mat["processo"] == "SLA/MSLA" else regras["hora_fdm"]
    custo_maquina_unit = horas_unit * hora_maquina

    # Preparação é cobrada uma vez por pedido (slicing, conferência e setup).
    # Acabamento é por peça e acompanha o nível de qualidade.
    custo_preparacao = CAT_PREPARACAO[categoria] * qual["mult"]
    custo_acabamento_unit = CAT_ACABAMENTO[categoria] * qual["mult"] * comp.get("risco_mult", 1.0)

    custo_producao = ((custo_material_unit + custo_maquina_unit + custo_acabamento_unit) * qtd) + custo_preparacao

    # Este é o valor que queremos que o parceiro receba líquido antes da
    # comissão da Voxxel: custos + margem operacional/lucro.
    margem_impressor = custo_producao * (regras["margem_impressor_pct"] / 100)
    liquido_parceiro_alvo = custo_producao + margem_impressor

    # Gross-up da comissão: em vez de tirar a comissão de um preço que já era
    # apertado, o preço ao cliente nasce de modo que, após a comissão, reste
    # o líquido-alvo do parceiro.
    comissao_pct = regras["comissao_voxxel_pct"] / 100
    preco_calculado = liquido_parceiro_alvo / max(0.01, 1 - comissao_pct)
    preco_total = max(regras["pedido_minimo"], preco_calculado) if volume_caixa > 0 else 0.0

    comissao_valor = preco_total * comissao_pct
    liquido_parceiro = preco_total - comissao_valor
    lucro_parceiro_estimado = max(0.0, liquido_parceiro - custo_producao)

    return {
        "preco_total": round(preco_total, 2),
        "horas_total": round(horas_unit * qtd, 2),
        "peso_total_g": round(peso_unit_g * qtd, 1),
        "custo_material": round(custo_material_unit * qtd, 2),
        "custo_maquina": round(custo_maquina_unit * qtd, 2),
        "custo_acabamento": round((custo_acabamento_unit * qtd) + custo_preparacao, 2),
        "custo_producao": round(custo_producao, 2),
        "liquido_parceiro": round(liquido_parceiro, 2),
        "lucro_parceiro_estimado": round(lucro_parceiro_estimado, 2),
        "comissao_voxxel": round(comissao_valor, 2),
        "material_nome": mat["nome"],
        "categoria_nome": CAT_NOME[categoria],
    }


def formatar_horas(h):
    if h < 1:
        return f"{round(h * 60)} min"
    horas = int(h)
    minutos = round((h - horas) * 60)
    return f"{horas}h" + (f" {minutos}min" if minutos > 0 else "")
