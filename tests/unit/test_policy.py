import pytest
from hypothesis import given
from hypothesis import strategies as st

from consig_risk.risk.policy import RiskPolicy, decide_term, max_term

POLICY = RiskPolicy(_env_file=None)  # type: ignore[call-arg]


@st.composite
def risk_curves(draw: st.DrawFn) -> dict[int, float]:
    """Curvas de risco acumulado válidas: entre 0 e 1 e nunca decrescentes com o prazo."""
    increments = draw(
        st.lists(
            st.floats(0, 0.2, allow_nan=False),
            min_size=len(POLICY.prazos()),
            max_size=len(POLICY.prazos()),
        )
    )
    curve, total = {}, 0.0
    for term, inc in zip(POLICY.prazos(), increments, strict=True):
        total = min(total + inc, 1.0)
        curve[term] = total
    return curve


def test_defaults_match_the_decision_in_adr_0004(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RISK_LIMITE_RISCO", "RISK_PRAZO_MAXIMO_MESES"):
        monkeypatch.delenv(var, raising=False)
    policy = RiskPolicy(_env_file=None)  # type: ignore[call-arg]

    assert policy.limite_risco == 0.10
    assert policy.prazo_maximo_meses == 36
    assert policy.prazos() == (6, 12, 18, 24, 30, 36)


def test_policy_is_configurable_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RISK_LIMITE_RISCO", "0.05")
    monkeypatch.setenv("RISK_PRAZO_MAXIMO_MESES", "48")

    policy = RiskPolicy(_env_file=None)  # type: ignore[call-arg]

    assert policy.limite_risco == 0.05
    assert policy.prazos()[-1] == 48


@pytest.mark.parametrize(
    ("risco", "nivel"),
    [
        (0.0, "baixo"),
        (0.05, "baixo"),
        (0.07, "moderado"),
        (0.10, "moderado"),
        (0.15, "alto"),
        (0.5, "muito alto"),
    ],
)
def test_risk_levels(risco: float, nivel: str) -> None:
    assert POLICY.nivel(risco) == nivel


def test_messages_use_brazilian_percent_format() -> None:
    assert decide_term(POLICY, 24, 0.021).motivo.startswith("risco de 2,1% até o fim do prazo")


def test_term_above_policy_maximum_is_refused_even_with_low_risk() -> None:
    decision = decide_term(POLICY, 48, 0.01)

    assert not decision.conceder
    assert "máximo da política" in decision.motivo


@given(risk_curves())
def test_max_term_respects_the_limit_and_is_the_longest_such_term(curve: dict[int, float]) -> None:
    maximo = max_term(POLICY, curve)

    if maximo is None:
        assert curve[POLICY.prazos()[0]] > POLICY.limite_risco
    else:
        assert curve[maximo] <= POLICY.limite_risco
        longer = [t for t in POLICY.prazos() if t > maximo]
        assert all(curve[t] > POLICY.limite_risco for t in longer)


@given(risk_curves())
def test_verdict_is_consistent_with_max_term(curve: dict[int, float]) -> None:
    """Pedir um prazo até o máximo aprovado concede; acima dele, recusa."""
    maximo = max_term(POLICY, curve)

    for term, risco in curve.items():
        decision = decide_term(POLICY, term, risco)
        assert decision.conceder == (maximo is not None and term <= maximo)
