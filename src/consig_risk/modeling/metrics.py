"""Métricas por horizonte.

Todas as empresas da coorte são acompanhadas até a mesma foto de desfecho (41 meses), então até
36 meses não há censura: "saiu até t?" é um rótulo binário observado para todas. Isso permite usar
métricas de classificação diretamente, sem os ajustes por censura (IPCW) que a análise de
sobrevivência normalmente exige.
"""

import numpy as np
import pandas as pd
from lifelines.utils import concordance_index
from sklearn.metrics import brier_score_loss, roc_auc_score


def horizon_labels(firms: pd.DataFrame, horizon: int, event_col: str = "evento") -> np.ndarray:
    observed = firms[event_col].to_numpy(dtype=bool) & (
        firms["duracao_meses"].to_numpy() <= horizon
    )
    return np.asarray(observed, dtype=int)


def horizon_metrics(
    firms: pd.DataFrame, survival: np.ndarray, horizons: tuple[int, ...]
) -> pd.DataFrame:
    """AUC, Brier, risco médio previsto e observado, por horizonte."""
    rows = []
    for j, h in enumerate(horizons):
        y = horizon_labels(firms, h)
        risk = 1 - survival[:, j]
        brier = brier_score_loss(y, risk)
        brier_ref = brier_score_loss(y, np.full_like(risk, y.mean()))
        rows.append(
            {
                "horizonte": h,
                "auc": roc_auc_score(y, risk) if 0 < y.sum() < len(y) else np.nan,
                "brier": brier,
                # Quanto o Brier melhora em relação a prever a taxa média para todos (0 = nada).
                "brier_skill": 1 - brier / brier_ref,
                "risco_previsto_medio": risk.mean(),
                "risco_observado": y.mean(),
            }
        )
    return pd.DataFrame(rows)


def calibration_table(
    firms: pd.DataFrame, survival: np.ndarray, horizons: tuple[int, ...], bins: int = 10
) -> pd.DataFrame:
    """Risco previsto x observado por decil de risco previsto, em cada horizonte."""
    parts = []
    for j, h in enumerate(horizons):
        risk = 1 - survival[:, j]
        df = pd.DataFrame({"previsto": risk, "observado": horizon_labels(firms, h)})
        df["faixa"] = pd.qcut(df["previsto"].rank(method="first"), bins, labels=False) + 1
        agg = df.groupby("faixa").agg(
            previsto=("previsto", "mean"),
            observado=("observado", "mean"),
            empresas=("previsto", "size"),
        )
        agg["horizonte"] = h
        parts.append(agg.reset_index())
    return pd.concat(parts, ignore_index=True)


def concordance(
    firms: pd.DataFrame, survival_at_last: np.ndarray, max_firms: int = 200_000
) -> float:
    """C-index de Harrell usando S(último horizonte) como escore (maior = sobrevive mais)."""
    idx = np.arange(len(firms))
    if len(idx) > max_firms:  # o C-index é O(n log n) mas consome memória; amostra estável
        idx = np.random.default_rng(0).choice(idx, max_firms, replace=False)
    return float(
        concordance_index(
            firms["duracao_meses"].to_numpy()[idx],
            survival_at_last[idx],
            firms["evento"].to_numpy()[idx],
        )
    )
