"""Dados sintéticos compartilhados pelos testes de modelagem e de avaliação de risco."""

import datetime as dt
import re
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import pandas as pd
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from consig_risk.modeling.artifact import RiskModel
from consig_risk.modeling.data import expand_pieces
from consig_risk.modeling.models import PiecewiseGBM, fit_categories
from consig_risk.modeling.spec import SOURCE_COLUMNS
from consig_risk.risk.assessment import RiskAssessor
from consig_risk.risk.policy import RiskPolicy
from consig_risk.risk.repository import SnapshotInfo

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


def company_row(cnpj_basico: str, **overrides: Any) -> dict[str, Any]:
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


def build_test_assessor() -> RiskAssessor:
    """Avaliador com o modelo sintético e um repositório com uma empresa de cada caso."""
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
            company_row(SAFE[:8]),
            company_row(RISKY[:8], divida_nao_previdenciaria=True),
            company_row(
                CLOSED[:8], situacao_cadastral=8, data_situacao_cadastral=dt.date(2025, 3, 1)
            ),
            company_row(MEI[:8], opcao_mei=True),
            company_row(PUBLIC[:8], grupo_natureza="1"),
            company_row(YOUNG[:8], idade_meses=10, capital_social=1e11),
        ]
    )
    return RiskAssessor(model, repo, RiskPolicy(_env_file=None))  # type: ignore[call-arg]


# --- LLM roteirizado -----------------------------------------------------------------------
# Comporta-se como um LLM disciplinado: chama `analisar_risco` com o CNPJ e o prazo da mensagem
# (ou `explicar_politica` se a pergunta for sobre o critério) e depois narra o relatório.

CNPJ_PATTERN = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\b\d{14}\b")
TERM_PATTERN = re.compile(r"(\d+)\s*meses")


class AuthenticationError(Exception):
    """Mesmo nome da exceção do LiteLLM para chave inválida."""


def _br(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def narrate(report: dict[str, Any], invent: bool = False) -> str:
    if report["status"] == "erro":
        return f"Não consegui analisar: {report['mensagem']}"
    if report.get("regra"):
        return f"Critério: {report['regra']}"
    empresa = report["empresa"]
    lines = [f"**Empresa:** {empresa['razao_social']} ({empresa['cnpj']})."]
    if report["veredito"]:
        decisao = "Conceder" if report["veredito"]["conceder"] else "Não conceder"
        lines.append(f"**Veredito:** {decisao}: {report['veredito']['motivo']}.")
    if report["prazo_maximo_aprovado_meses"]:
        lines.append(f"Prazo máximo aprovado: {report['prazo_maximo_aprovado_meses']} meses.")
    for point in report["curva"]:
        lines.append(f"- {point['prazo_meses']} meses: {_br(point['risco_pct'])}%")
    if invent:
        lines.append("Em 99 meses o risco seria de 37,7%.")
    return "\n".join(lines)


class ScriptedLlm(BaseLlm):
    model: str = "scripted"
    invent: bool = False
    fail: bool = False

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.fail:
            raise AuthenticationError("invalid api key")
        last = llm_request.contents[-1]
        responses = [p.function_response for p in last.parts or [] if p.function_response]
        if responses:
            text = narrate(dict(responses[0].response or {}), self.invent)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))
            return
        message = "".join(p.text or "" for p in last.parts or [])
        if "critério" in message.lower():
            call = types.FunctionCall(name="explicar_politica", args={})
        else:
            args: dict[str, Any] = {}
            if cnpj := CNPJ_PATTERN.search(message):
                args["cnpj"] = cnpj.group()
            if term := TERM_PATTERN.search(message):
                args["prazo_meses"] = int(term.group(1))
            call = types.FunctionCall(name="analisar_risco", args=args)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(function_call=call)])
        )
