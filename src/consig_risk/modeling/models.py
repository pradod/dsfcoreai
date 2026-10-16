"""Modelos candidatos, todos com a mesma interface de previsão: S(t) por empresa e horizonte.

- `KaplanMeierBaseline`: curva da população, sem features. É o piso: qualquer modelo precisa
  superá-lo.
- `CoxBaseline`: Cox de riscos proporcionais (lifelines). É o modelo clássico de sobrevivência,
  mas supõe que o efeito de cada feature é o mesmo em todo o prazo, o que a EDA contradiz.
- `PiecewiseGLM`: exponencial por partes com regressão de Poisson linear. Interpretável.
- `PiecewiseGBM`: exponencial por partes com LightGBM (objetivo Poisson). Captura interações e
  efeitos que mudam com o período e a idade.

Nos dois modelos por partes, a taxa mensal é estimada com alvo `eventos / exposição` e peso
`exposição`, o que equivale a uma regressão de Poisson com offset log(exposição).
"""

from collections.abc import Callable
from typing import Any, Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, SplineTransformer

from consig_risk.modeling.data import prediction_pieces
from consig_risk.modeling.spec import CATEGORICAL, FEATURE_NAMES, FEATURES

SEED = 42


class SurvivalModel(Protocol):
    name: str

    def survival(self, firms: pd.DataFrame, horizons: tuple[int, ...]) -> np.ndarray:
        """Matriz (empresas × horizontes) com a probabilidade de continuar ativa."""
        ...


def prepare_features(df: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    """Converte para os tipos do modelo, com categorias fixas (desconhecidas viram NaN)."""
    out = pd.DataFrame(index=df.index)
    for f in FEATURES:
        col = df[f.name]
        if f.kind == "categorical":
            out[f.name] = pd.Categorical(col.astype("string"), categories=categories[f.name])
        elif f.kind == "boolean":
            out[f.name] = col.astype(float)
        else:
            values = pd.to_numeric(col, errors="coerce").astype(float)
            out[f.name] = np.log1p(values.clip(lower=0)) if f.log else values
    return out


def fit_categories(df: pd.DataFrame, min_count: int = 200) -> dict[str, list[str]]:
    """Níveis de cada categórica vistos no treino com frequência mínima; o resto vira NaN."""
    categories = {}
    for name in CATEGORICAL:
        counts = df[name].astype("string").value_counts()
        categories[name] = sorted(counts[counts >= min_count].index.tolist())
    return categories


class KaplanMeierBaseline:
    name = "Kaplan-Meier (sem features)"

    def fit(self, firms: pd.DataFrame) -> "KaplanMeierBaseline":
        self._km = KaplanMeierFitter().fit(firms["duracao_meses"], firms["evento"])
        return self

    def survival(self, firms: pd.DataFrame, horizons: tuple[int, ...]) -> np.ndarray:
        values = np.array([float(self._km.predict(h)) for h in horizons])
        return np.tile(values, (len(firms), 1))


class CoxBaseline:
    """Cox com as mesmas features (categóricas em one-hot), ajustado numa amostra menor."""

    name = "Cox de riscos proporcionais"

    def fit(self, firms: pd.DataFrame, categories: dict[str, list[str]]) -> "CoxBaseline":
        self._categories = categories
        design = self._design(firms)
        # Em amostras pequenas, categorias raras viram colunas constantes, que tornam a matriz
        # singular para o Cox: ficam de fora.
        design = design.loc[:, design.nunique() > 1]
        self._columns = list(design.columns)
        design["duracao_meses"] = firms["duracao_meses"].to_numpy()
        design["evento"] = firms["evento"].to_numpy()
        self.cph = CoxPHFitter(penalizer=0.01).fit(design, "duracao_meses", "evento")
        return self

    def design(self, firms: pd.DataFrame) -> pd.DataFrame:
        """Matriz de covariáveis usada no ajuste, para diagnósticos (ex.: teste de Schoenfeld)."""
        return self._design(firms)

    def _design(self, firms: pd.DataFrame) -> pd.DataFrame:
        feats = firms.assign(idade_no_periodo=firms["idade_meses"], periodo=1)
        x = prepare_features(feats, self._categories).drop(columns=["periodo"])
        x = x.drop(columns=["natureza_juridica", "cnae_divisao"])  # alta cardinalidade
        numeric = x.select_dtypes("number").fillna(0)
        dummies = pd.get_dummies(x.select_dtypes("category"), drop_first=True, dtype=float)
        design = pd.concat([numeric, dummies], axis=1).reset_index(drop=True)
        if hasattr(self, "_columns"):
            design = design.reindex(columns=self._columns, fill_value=0.0)
        return design

    def survival(self, firms: pd.DataFrame, horizons: tuple[int, ...]) -> np.ndarray:
        sf = self.cph.predict_survival_function(self._design(firms), times=list(horizons))
        return np.asarray(sf.T.to_numpy())


class _Piecewise:
    """Base dos modelos por partes: soma a taxa × exposição de cada período até o horizonte."""

    categories: dict[str, list[str]]

    def rate(self, pieces: pd.DataFrame) -> np.ndarray:  # taxa mensal por linha
        raise NotImplementedError

    def cumulative_hazard(self, firms: pd.DataFrame, horizon: int) -> np.ndarray:
        pieces = prediction_pieces(firms, horizon)
        contrib = self.rate(pieces) * pieces["exposicao"].to_numpy()
        return np.bincount(pieces["_firm"].to_numpy(), weights=contrib, minlength=len(firms))

    def survival(self, firms: pd.DataFrame, horizons: tuple[int, ...]) -> np.ndarray:
        firms = firms.reset_index(drop=True)
        return np.column_stack([np.exp(-self.cumulative_hazard(firms, h)) for h in horizons])


def _fill_nan(values: Any) -> np.ndarray:
    return np.nan_to_num(np.asarray(values, dtype=float), nan=0.0)


class PiecewiseGLM(_Piecewise):
    name = "Exponencial por partes + Poisson linear (GLM)"

    def fit(self, pieces: pd.DataFrame, categories: dict[str, list[str]]) -> "PiecewiseGLM":
        self.categories = categories
        numeric = [f.name for f in FEATURES if f.kind != "categorical"]
        fill = FunctionTransformer(_fill_nan)
        self.pipeline = Pipeline(
            [
                (
                    "features",
                    ColumnTransformer(
                        [
                            (
                                "idade",
                                Pipeline(
                                    [
                                        ("fill", fill),
                                        ("spline", SplineTransformer(n_knots=6, degree=3)),
                                    ]
                                ),
                                ["idade_no_periodo"],
                            ),
                            (
                                "num",
                                fill,
                                [n for n in numeric if n not in ("idade_no_periodo", "periodo")],
                            ),
                            (
                                # O período entra como categoria: a taxa de cada ano é livre.
                                "cat",
                                OneHotEncoder(handle_unknown="ignore", min_frequency=200),
                                [*CATEGORICAL, "periodo"],
                            ),
                        ],
                        sparse_threshold=1.0,
                    ),
                ),
                ("poisson", PoissonRegressor(alpha=1e-4, max_iter=300)),
            ]
        )
        x = self._x(pieces)
        y = pieces["eventos"] / pieces["exposicao"]
        self.pipeline.fit(x, y, poisson__sample_weight=pieces["exposicao"])
        return self

    def _x(self, pieces: pd.DataFrame) -> pd.DataFrame:
        x = prepare_features(pieces, self.categories)
        for name in CATEGORICAL:  # o OneHotEncoder trata NaN como uma categoria própria
            x[name] = x[name].astype("string").fillna("(outra)")
        x["periodo"] = x["periodo"].astype(int).astype(str)
        return x

    def rate(self, pieces: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.pipeline.predict(self._x(pieces)))


class PiecewiseGBM(_Piecewise):
    name = "Exponencial por partes + LightGBM (Poisson)"

    PARAMS: dict[str, Any] = {  # noqa: RUF012
        "objective": "poisson",
        "learning_rate": 0.1,
        "num_leaves": 63,
        "min_data_in_leaf": 500,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "cat_smooth": 50,
        "max_cat_to_onehot": 8,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": 4,
        "verbose": -1,
    }

    def fit(
        self,
        pieces: pd.DataFrame,
        categories: dict[str, list[str]],
        valid_pieces: pd.DataFrame | None = None,
        num_boost_round: int = 2000,
    ) -> "PiecewiseGBM":
        self.categories = categories
        train = self._dataset(pieces)
        valid_sets: list[lgb.Dataset] = []
        callbacks: list[Callable[..., Any]] = []
        if valid_pieces is not None:
            valid_sets = [self._dataset(valid_pieces, reference=train)]
            callbacks = [lgb.early_stopping(50, verbose=False)]
        self.booster = lgb.train(
            self.PARAMS,
            train,
            num_boost_round=num_boost_round,
            valid_sets=valid_sets,
            callbacks=callbacks,
        )
        return self

    def _dataset(self, pieces: pd.DataFrame, reference: lgb.Dataset | None = None) -> lgb.Dataset:
        return lgb.Dataset(
            prepare_features(pieces, self.categories),
            label=pieces["eventos"] / pieces["exposicao"],
            weight=pieces["exposicao"],
            categorical_feature=list(CATEGORICAL),
            reference=reference,
            free_raw_data=False,
        )

    def rate(self, pieces: pd.DataFrame) -> np.ndarray:
        x = prepare_features(pieces, self.categories)[list(FEATURE_NAMES)]
        return np.asarray(self.booster.predict(x, num_iteration=self.booster.best_iteration))
