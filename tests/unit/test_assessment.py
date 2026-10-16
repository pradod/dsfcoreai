from typing import Any

import pytest

from consig_risk.risk.assessment import CompanyNotFoundError, RiskAssessor, format_value
from consig_risk.risk.cnpj import InvalidCNPJError
from tests.factories import (
    CLOSED,
    MEI,
    PUBLIC,
    RISKY,
    SAFE,
    YOUNG,
    build_test_assessor,
)


@pytest.fixture(scope="module")
def assessor() -> RiskAssessor:
    return build_test_assessor()


def test_low_risk_company_gets_credit_for_requested_term(assessor: RiskAssessor) -> None:
    # Taxa sintética de 0,5%/mês: ~5,8% em 12 meses, dentro do limite de 10%.
    report = assessor.assess("00.000.000/0001-91", prazo_meses=12)

    assert report.resultado == "avaliada"
    assert report.veredito is not None and report.veredito.conceder
    point = next(p for p in report.curva if p.prazo_meses == 12)
    assert point.dentro_do_limite and point.risco_pct <= 10
    maximo = report.prazo_maximo_aprovado_meses
    assert maximo is not None and 12 <= maximo < 36
    assert report.limitacoes and report.fonte["data_referencia_dados"] == "11/10/2026"


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

    assert report.resultado == "nao_elegivel"
    assert report.veredito is not None and not report.veredito.conceder
    assert "baixada" in report.veredito.motivo and "01/03/2025" in report.veredito.motivo
    assert report.curva == []


@pytest.mark.parametrize(("cnpj", "reason"), [(MEI, "MEI"), (PUBLIC, "administração pública")])
def test_out_of_scope_companies_get_no_verdict(
    assessor: RiskAssessor, cnpj: str, reason: str
) -> None:
    report = assessor.assess(cnpj, prazo_meses=12)

    assert report.resultado == "fora_do_escopo"
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
