from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from consig_risk.modeling.artifact import RiskModel
from consig_risk.modeling.data import expand_pieces, prediction_pieces
from consig_risk.modeling.metrics import horizon_labels, horizon_metrics
from consig_risk.modeling.models import PiecewiseGBM
from tests.factories import firms as _firms
from tests.factories import synthetic_cohort as _synthetic_cohort
from tests.factories import train_synthetic_model


def test_expand_pieces_splits_exposure_and_places_event_in_right_period() -> None:
    firms = _firms(3)
    firms["evento"] = [True, True, False]
    firms["duracao_meses"] = [5.0, 30.0, 41.0]

    pieces = expand_pieces(firms)

    assert len(pieces) == 1 + 3 + 3
    assert pieces["exposicao"].sum() == pytest.approx(5 + 30 + 41)
    assert pieces["eventos"].sum() == 2
    event_rows = pieces[pieces["eventos"] == 1]
    assert sorted(event_rows["periodo"]) == [1, 3]
    last = pieces[pieces["periodo"] == 3]
    assert (last["idade_no_periodo"] == 60 + 24).all()


@pytest.mark.parametrize("horizon", [6, 12, 30, 36, 48, 96])
def test_prediction_pieces_cover_the_horizon_and_age_advances(horizon: int) -> None:
    pieces = prediction_pieces(_firms(2), horizon)

    one = pieces[pieces["_firm"] == 0]
    assert one["exposicao"].sum() == pytest.approx(horizon)
    assert one["periodo"].max() <= 3
    assert one["idade_no_periodo"].is_monotonic_increasing


@pytest.fixture(scope="module")
def trained() -> tuple[PiecewiseGBM, pd.DataFrame]:
    cohort = _synthetic_cohort()
    return train_synthetic_model(cohort), cohort


def test_survival_is_a_valid_non_increasing_curve(
    trained: tuple[PiecewiseGBM, pd.DataFrame],
) -> None:
    model, cohort = trained

    s = model.survival(cohort.head(50), (6, 12, 24, 36, 60, 96))

    assert ((s > 0) & (s <= 1)).all()
    assert (np.diff(s, axis=1) <= 1e-12).all()


def test_model_recovers_known_monthly_rates(trained: tuple[PiecewiseGBM, pd.DataFrame]) -> None:
    model, _ = trained

    s12 = model.survival(pd.concat([_firms(1), _firms(1, divida_nao_previdenciaria=True)]), (12,))[
        :, 0
    ]

    assert s12[0] == pytest.approx(np.exp(-0.005 * 12), abs=0.02)
    assert s12[1] == pytest.approx(np.exp(-0.02 * 12), abs=0.04)


def test_artifact_roundtrip_and_drivers(
    trained: tuple[PiecewiseGBM, pd.DataFrame], tmp_path: Path
) -> None:
    model, cohort = trained
    risk_model = RiskModel(model, {"modelo": "teste"})
    risk_model.save(tmp_path)

    loaded = RiskModel.load(tmp_path)
    firm = _firms(1, divida_nao_previdenciaria=True)

    np.testing.assert_allclose(
        loaded.survival(cohort.head(20), (12, 36)), model.survival(cohort.head(20), (12, 36))
    )
    drivers = loaded.drivers(firm, horizon=24, top=3)
    assert drivers[0].feature == "divida_nao_previdenciaria"
    assert drivers[0].contribution > 0  # dívida aumenta o risco
    assert all(d.feature != "periodo" for d in drivers)
    assert [abs(d.contribution) for d in drivers] == sorted(
        (abs(d.contribution) for d in drivers), reverse=True
    )


def test_artifact_load_explains_how_to_train_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"consig_risk\.modeling\.train"):
        RiskModel.load(tmp_path)


def test_horizon_metrics_use_observed_binary_labels() -> None:
    firms = _firms(4)
    firms["evento"] = [True, True, False, False]
    firms["duracao_meses"] = [3.0, 20.0, 41.0, 41.0]
    survival = np.array([[0.2, 0.1], [0.6, 0.3], [0.9, 0.8], [0.95, 0.9]])

    m = horizon_metrics(firms, survival, (12, 24))

    assert list(horizon_labels(firms, 12)) == [1, 0, 0, 0]
    assert m.loc[m.horizonte == 12, "auc"].item() == 1.0
    assert m.loc[m.horizonte == 24, "risco_observado"].item() == 0.5
