"""Dados sintéticos compartilhados pelos testes de modelagem e de avaliação de risco."""

import numpy as np
import pandas as pd

from consig_risk.modeling.data import expand_pieces
from consig_risk.modeling.models import PiecewiseGBM, fit_categories
from consig_risk.modeling.spec import SOURCE_COLUMNS

DEFAULTS = {
    "idade_meses": 60,
    "capital_social": 10_000.0,
    "n_cnaes_secundarios": 1,
    "n_filiais_ativas": 0,
    "n_ufs": 1,
    "n_socios": 2,
    "n_socios_entrada_12m": 0,
    "n_inscricoes_divida": 0,
    "valor_divida": 0.0,
    "meses_desde_ultima_inscricao": np.nan,
    "tem_situacao_especial": False,
    "opcao_simples": True,
    "excluido_simples_12m": False,
    "ex_mei": False,
    "tem_socio_pj": False,
    "tem_socio_estrangeiro": False,
    "divida_fgts": False,
    "divida_previdenciaria": False,
    "divida_nao_previdenciaria": False,
    "divida_ajuizada": False,
    "regime_tributario": "simples",
    "porte": "01",
    "grupo_natureza": "2",
    "natureza_juridica": "2062",
    "cnae_secao": "G",
    "cnae_divisao": "47",
    "uf": "SP",
}


def firms(n: int, **overrides: object) -> pd.DataFrame:
    df = pd.DataFrame({c: [DEFAULTS[c]] * n for c in SOURCE_COLUMNS})
    for k, v in overrides.items():
        df[k] = v
    return df


def synthetic_cohort(n_per_group: int = 3000, seed: int = 0) -> pd.DataFrame:
    """Dois grupos com taxa mensal constante conhecida: sem dívida 0,5%/mês, com dívida 2%/mês."""
    rng = np.random.default_rng(seed)
    parts = []
    for debt, rate in ((False, 0.005), (True, 0.02)):
        g = firms(n_per_group, divida_nao_previdenciaria=debt)
        t = rng.exponential(1 / rate, n_per_group)
        g["evento"] = t <= 41
        g["duracao_meses"] = np.minimum(t, 41)
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def train_synthetic_model(cohort: pd.DataFrame) -> PiecewiseGBM:
    """GBM pequeno e rápido treinado na coorte sintética (taxas conhecidas)."""
    model = PiecewiseGBM()
    model.PARAMS = {**PiecewiseGBM.PARAMS, "min_data_in_leaf": 20, "num_threads": 1}
    return model.fit(
        expand_pieces(cohort), fit_categories(cohort, min_count=1), num_boost_round=200
    )
