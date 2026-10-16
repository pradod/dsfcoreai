import datetime as dt
from typing import Any

import pytest

from consig_risk.modeling.artifact import RiskModel
from consig_risk.risk.assessment import CompanyNotFoundError, RiskAssessor, format_value
from consig_risk.risk.cnpj import InvalidCNPJError
from consig_risk.risk.policy import RiskPolicy
from consig_risk.risk.repository import SnapshotInfo
from tests.factories import DEFAULTS, synthetic_cohort, train_synthetic_model

SNAPSHOT = SnapshotInfo("2026-10-11", "2026-10", "2026_trimestre_02", 2024)

# CNPJs com dígitos verificadores válidos (o primeiro é o do Banco do Brasil).
SAFE, RISKY, CLOSED, MEI, PUBLIC, YOUNG = (
    "00000000000191",
    "11222333000181",
    "11444777000161",
    "12345678000195",
    "45723174000110",
    "33000167000101",
)


def _row(cnpj_basico: str, **overrides: Any) -> dict[str, Any]:
    row = {
        **DEFAULTS,
        "cnpj_basico": cnpj_basico,
        "razao_social": f"EMPRESA {cnpj_basico}",
        "situacao_cadastral": 2,
        "data_situacao_cadastral": dt.date(2010, 1, 1),
        "data_inicio_atividade": dt.date(2005, 1, 1),
        "natureza_descricao": "Sociedade Empresária Limitada",
        "cnae_descricao": "Comércio varejista",
        "opcao_mei": False,
        "n_filiais_ativas": 0,
    }
    row.update(overrides)
    return row


class FakeRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = {r["cnpj_basico"]: r for r in rows}

    @property
    def snapshot(self) -> SnapshotInfo:
        return SNAPSHOT

    def get(self, cnpj_basico: str) -> dict[str, Any] | None:
        return self._rows.get(cnpj_basico)


@pytest.fixture(scope="module")
def assessor() -> RiskAssessor:
    model = RiskModel(
        train_synthetic_model(synthetic_cohort()),
        {
            "modelo": "teste",
            "horizonte_maximo_validado_meses": 36,
            "faixas_treino": {"capital_social": {"p001": 0.0, "p999": 1e7}},
        },
    )
    repo = FakeRepository(
        [
            _row(SAFE[:8]),
            _row(RISKY[:8], divida_nao_previdenciaria=True),
            _row(CLOSED[:8], situacao_cadastral=8, data_situacao_cadastral=dt.date(2025, 3, 1)),
            _row(MEI[:8], opcao_mei=True),
            _row(PUBLIC[:8], grupo_natureza="1"),
            _row(YOUNG[:8], idade_meses=10, capital_social=1e11),
        ]
    )
    return RiskAssessor(model, repo, RiskPolicy(_env_file=None))  # type: ignore[call-arg]


def test_low_risk_company_gets_credit_for_requested_term(assessor: RiskAssessor) -> None:
    # Taxa sintética de 0,5%/mês: ~5,8% em 12 meses, dentro do limite de 10%.
    report = assessor.assess("00.000.000/0001-91", prazo_meses=12)

    assert report.status == "avaliada"
    assert report.veredito is not None and report.veredito.conceder
    point = next(p for p in report.curva if p.prazo_meses == 12)
    assert point.dentro_do_limite and point.risco_pct <= 10
    maximo = report.prazo_maximo_aprovado_meses
    assert maximo is not None and 12 <= maximo < 36
    assert report.limitacoes and report.fonte["data_referencia_dados"] == "2026-10-11"


def test_risky_company_is_refused_and_gets_a_shorter_max_term(assessor: RiskAssessor) -> None:
    report = assessor.assess(RISKY, prazo_meses=36)

    assert report.veredito is not None and not report.veredito.conceder
    assert "acima do limite" in report.veredito.motivo
    maximo = report.prazo_maximo_aprovado_meses
    assert maximo is None or maximo < 36
    assert report.fatores[0].fator == "dívida ativa não previdenciária"
    assert report.fatores[0].efeito == "aumenta o risco"


def test_curve_is_non_decreasing_and_marks_extrapolation(assessor: RiskAssessor) -> None:
    report = assessor.assess(SAFE, prazo_meses=48)

    risks = [p.risco_pct for p in report.curva]
    assert risks == sorted(risks)
    assert [p.prazo_meses for p in report.curva if p.extrapolado] == [48]
    assert report.veredito is not None and not report.veredito.conceder
    assert any("extrapolado" in a for a in report.avisos)


def test_open_request_reports_max_term_without_verdict(assessor: RiskAssessor) -> None:
    report = assessor.assess(SAFE)

    assert report.veredito is None
    assert report.prazo_maximo_aprovado_meses is not None
    assert report.nivel_risco in {"baixo", "moderado", "alto", "muito alto"}


def test_inactive_company_is_not_eligible(assessor: RiskAssessor) -> None:
    report = assessor.assess(CLOSED, prazo_meses=12)

    assert report.status == "nao_elegivel"
    assert report.veredito is not None and not report.veredito.conceder
    assert "baixada" in report.veredito.motivo and "01/03/2025" in report.veredito.motivo
    assert report.curva == []


@pytest.mark.parametrize(("cnpj", "reason"), [(MEI, "MEI"), (PUBLIC, "administração pública")])
def test_out_of_scope_companies_get_no_verdict(
    assessor: RiskAssessor, cnpj: str, reason: str
) -> None:
    report = assessor.assess(cnpj, prazo_meses=12)

    assert report.status == "fora_do_escopo"
    assert report.veredito is None
    assert any(reason in a for a in report.avisos)


def test_young_company_and_out_of_range_values_raise_warnings(assessor: RiskAssessor) -> None:
    report = assessor.assess(YOUNG, prazo_meses=12)

    assert any("menos de 3 anos" in a for a in report.avisos)
    assert any("fora da faixa" in a and "capital social" in a for a in report.avisos)


def test_branch_cnpj_is_analyzed_as_the_company(assessor: RiskAssessor) -> None:
    report = assessor.assess("00.000.000/0002-72", prazo_meses=12)

    assert report.empresa.cnpj_basico == "00000000"
    assert any("filial" in a for a in report.avisos)


def test_invalid_and_unknown_cnpj_raise_specific_errors(assessor: RiskAssessor) -> None:
    with pytest.raises(InvalidCNPJError):
        assessor.assess("00.000.000/0001-92")
    with pytest.raises(CompanyNotFoundError, match="não foi encontrado"):
        assessor.assess("98.765.432/0001-98")


@pytest.mark.parametrize(
    ("feature", "value", "expected"),
    [
        ("idade_meses", 722, "60 anos e 2 meses"),
        ("idade_meses", 12, "1 ano"),
        ("idade_meses", 5, "5 meses"),
        ("capital_social", 120_000_000_000.0, "R$ 120.000.000.000,00"),
        ("regime_tributario", "sem_ecf", "não entregou a ECF estando fora do Simples"),
        ("divida_fgts", True, "sim"),
        ("meses_desde_ultima_inscricao", float("nan"), "sem dívida inscrita"),
        ("cnae_secao", "K", "atividades financeiras"),
        ("n_filiais_ativas", 5427, "5.427"),
    ],
)
def test_values_are_reported_in_business_language(feature: str, value: Any, expected: str) -> None:
    assert format_value(feature, value, {}) == expected
