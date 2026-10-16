"""Artefato do modelo: o que o treino grava e o agent carrega.

Conteúdo de `artifacts/model/`:
    model.txt       árvores do LightGBM (formato texto, legível e versionável)
    spec.json       categorias aceitas, features, períodos e parâmetros de treino
    metadata.json   dados de origem, splits, métricas de validação e versões das bibliotecas
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from consig_risk.modeling.data import prediction_pieces
from consig_risk.modeling.models import PiecewiseGBM, prepare_features
from consig_risk.modeling.spec import FEATURE_NAMES, LABELS

# "periodo" é o ano do crédito, não uma característica da empresa: não entra nas explicações.
_NOT_EXPLAINED = frozenset({"periodo"})


@dataclass(frozen=True)
class Driver:
    """Uma feature e quanto ela empurra o risco da empresa para cima (>0) ou para baixo (<0)."""

    feature: str
    label: str
    value: Any
    contribution: float  # em log da taxa mensal; exp(contribution) é o fator multiplicativo


class RiskModel:
    """Modelo treinado + metadados. Prevê a curva de sobrevivência e explica cada previsão."""

    def __init__(self, gbm: PiecewiseGBM, metadata: dict[str, Any]) -> None:
        self.gbm = gbm
        self.metadata = metadata

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.gbm.booster.save_model(
            directory / "model.txt", num_iteration=self.gbm.booster.best_iteration
        )
        spec = {
            "features": list(FEATURE_NAMES),
            "categories": self.gbm.categories,
            "params": self.gbm.PARAMS,
            "best_iteration": self.gbm.booster.best_iteration,
        }
        (directory / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False))
        (directory / "metadata.json").write_text(
            json.dumps(self.metadata, indent=2, ensure_ascii=False, default=str)
        )

    @classmethod
    def load(cls, directory: Path) -> "RiskModel":
        if not (directory / "model.txt").exists():
            raise FileNotFoundError(
                f"modelo não encontrado em {directory}; rode `python -m consig_risk.modeling.train`"
            )
        spec = json.loads((directory / "spec.json").read_text())
        gbm = PiecewiseGBM()
        gbm.categories = spec["categories"]
        gbm.booster = lgb.Booster(model_file=str(directory / "model.txt"))
        metadata = json.loads((directory / "metadata.json").read_text())
        return cls(gbm, metadata)

    def survival(self, firms: pd.DataFrame, horizons: tuple[int, ...]) -> np.ndarray:
        return self.gbm.survival(firms, horizons)

    def drivers(self, firm: pd.DataFrame, horizon: int, top: int = 5) -> list[Driver]:
        """Features que mais pesam no risco de uma empresa até `horizon` meses.

        Usa as contribuições SHAP do LightGBM (em log da taxa mensal) de cada período do prazo,
        ponderadas pela exposição de cada período.
        """
        if len(firm) != 1:
            raise ValueError("drivers() explica uma empresa por vez")
        pieces = prediction_pieces(firm.reset_index(drop=True), horizon)
        x = prepare_features(pieces, self.gbm.categories)[list(FEATURE_NAMES)]
        contrib = np.asarray(self.gbm.booster.predict(x, pred_contrib=True))[
            :, :-1
        ]  # última coluna = base
        weights = pieces["exposicao"].to_numpy()
        mean_contrib = (contrib * weights[:, None]).sum(axis=0) / weights.sum()
        ranked = sorted(
            (
                Driver(name, LABELS[name], firm.iloc[0].get(name), float(c))
                for name, c in zip(FEATURE_NAMES, mean_contrib, strict=True)
                if name not in _NOT_EXPLAINED and name != "idade_no_periodo"
            ),
            key=lambda d: abs(d.contribution),
            reverse=True,
        )
        # A idade aparece com o valor atual da empresa (em meses), não o do período.
        age = float(mean_contrib[FEATURE_NAMES.index("idade_no_periodo")])
        age_driver = Driver(
            "idade_meses", LABELS["idade_no_periodo"], firm.iloc[0]["idade_meses"], age
        )
        ranked = sorted([*ranked, age_driver], key=lambda d: abs(d.contribution), reverse=True)
        return ranked[:top]
