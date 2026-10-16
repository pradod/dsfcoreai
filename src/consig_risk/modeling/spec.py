"""O que entra no modelo e como o tempo é dividido.

Modelo exponencial por partes: o acompanhamento de cada empresa é cortado em períodos e, dentro de
cada período, o risco é uma taxa mensal constante que depende das features da empresa e da idade
dela no início do período. Os lotes anuais de inaptidão da Receita (ver EDA, seção 3) ficam
diluídos dentro de cada período, e o modelo não aprende o calendário de 2023–2026.
"""

from dataclasses import dataclass

# Períodos em meses desde o início do crédito: ano 1, ano 2 e "ano 3 em diante". O último período
# vai até o fim do acompanhamento no treino (41 meses) e, na previsão, se repete a cada 12 meses
# com a idade da empresa atualizada.
PIECE_STARTS: tuple[int, ...] = (0, 12, 24)
PIECE_LENGTH_AFTER_LAST = 12

HORIZONS: tuple[int, ...] = (12, 24, 36)
# Fim do acompanhamento. Até 36 meses a janela observada teve só 2 dos 3 lotes anuais de inaptidão
# (o de 2025 veio acumulado em mai/2026, no mês 36–37); aos 41 meses o calendário está completo,
# e é nesse horizonte que a calibração do risco médio pode ser conferida sem esse efeito.
EVALUATION_HORIZONS: tuple[int, ...] = (*HORIZONS, 41)


@dataclass(frozen=True)
class Feature:
    name: str
    kind: str  # "numeric" | "boolean" | "categorical"
    label: str  # nome legível, usado nas explicações do agent
    log: bool = False  # aplica log1p (valores monetários e contagens com cauda longa)


FEATURES: tuple[Feature, ...] = (
    Feature("idade_no_periodo", "numeric", "idade da empresa"),
    Feature("periodo", "numeric", "ano do crédito"),
    Feature("capital_social", "numeric", "capital social", log=True),
    Feature("n_cnaes_secundarios", "numeric", "nº de atividades secundárias"),
    Feature("n_filiais_ativas", "numeric", "nº de filiais ativas", log=True),
    Feature("n_ufs", "numeric", "nº de UFs com estabelecimento"),
    Feature("n_socios", "numeric", "nº de sócios"),
    Feature("n_socios_entrada_12m", "numeric", "sócios que entraram nos últimos 12 meses"),
    Feature("n_inscricoes_divida", "numeric", "nº de inscrições em dívida ativa", log=True),
    Feature("valor_divida", "numeric", "valor da dívida ativa", log=True),
    Feature("meses_desde_ultima_inscricao", "numeric", "meses desde a última inscrição em dívida"),
    Feature("tem_situacao_especial", "boolean", "situação especial (ex.: recuperação judicial)"),
    Feature("opcao_simples", "boolean", "optante do Simples"),
    Feature("excluido_simples_12m", "boolean", "excluída do Simples nos últimos 12 meses"),
    Feature("ex_mei", "boolean", "ex-MEI"),
    Feature("tem_socio_pj", "boolean", "tem sócio pessoa jurídica"),
    Feature("tem_socio_estrangeiro", "boolean", "tem sócio estrangeiro"),
    Feature("divida_fgts", "boolean", "dívida ativa de FGTS"),
    Feature("divida_previdenciaria", "boolean", "dívida ativa previdenciária"),
    Feature("divida_nao_previdenciaria", "boolean", "dívida ativa não previdenciária"),
    Feature("divida_ajuizada", "boolean", "dívida em execução judicial"),
    Feature("regime_tributario", "categorical", "regime tributário"),
    Feature("porte", "categorical", "porte"),
    Feature("grupo_natureza", "categorical", "grupo de natureza jurídica"),
    Feature("natureza_juridica", "categorical", "natureza jurídica"),
    Feature("cnae_secao", "categorical", "setor (seção CNAE)"),
    Feature("cnae_divisao", "categorical", "atividade (divisão CNAE)"),
    Feature("uf", "categorical", "UF da sede"),
)

FEATURE_NAMES: tuple[str, ...] = tuple(f.name for f in FEATURES)
CATEGORICAL: tuple[str, ...] = tuple(f.name for f in FEATURES if f.kind == "categorical")
LABELS: dict[str, str] = {f.name: f.label for f in FEATURES}

# Colunas da coorte/tabela de empresas de que o modelo precisa (as features dependentes do
# período, idade_no_periodo e periodo, são derivadas de idade_meses).
SOURCE_COLUMNS: tuple[str, ...] = (
    "idade_meses",
    *(n for n in FEATURE_NAMES if n not in ("idade_no_periodo", "periodo")),
)
