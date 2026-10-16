"""Avaliação do agent com o LLM configurado no .env, sobre os dados e o modelo reais.

Uso:
    uv run pytest -m llm

Fica fora da execução padrão (custa chamadas ao provedor e exige dados preparados). Cada caso
confere o que é verificável de forma objetiva: a ferramenta chamada, os argumentos, o resultado
do relatório e se a resposta tem números sem origem. O texto em si não é comparado palavra por
palavra, porque varia entre provedores.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from consig_risk.agent.service import AgentError
from consig_risk.config import LLMSettings, get_settings

pytestmark = pytest.mark.llm

RATE_LIMIT_RETRIES = 4
RATE_LIMIT_WAIT_SECONDS = 60


@dataclass(frozen=True)
class Step:
    mensagem: str
    ferramenta: str | None
    argumentos: dict[str, Any] = field(default_factory=dict)
    status_ferramenta: str | None = "ok"
    resultado: str | None = None  # campo `resultado` do relatório
    conceder: bool | None = None


CONVERSAS: dict[str, list[Step]] = {
    "prazo informado e acompanhamento": [
        Step(
            "Analise o CNPJ 00.000.000/0001-91 para um consignado de 24 meses.",
            "analisar_risco",
            {"prazo_meses": 24},
            resultado="avaliada",
            conceder=True,
        ),
        Step("E se fossem 12 meses?", "analisar_risco", {"prazo_meses": 12}, conceder=True),
        Step("E se fossem 48 meses?", "analisar_risco", {"prazo_meses": 48}, conceder=False),
        Step("Por que esse prazo máximo?", "explicar_politica"),
    ],
    "prazo em aberto": [
        Step(
            "Faça o relatório de risco do CNPJ 20.000.020/0001-70.",
            "analisar_risco",
            resultado="avaliada",
        ),
    ],
    "empresa baixada": [
        Step(
            "Analise o CNPJ 30.000.011/0001-32 para 12 meses.",
            "analisar_risco",
            resultado="nao_elegivel",
            conceder=False,
        ),
    ],
    "MEI fora do escopo": [
        Step(
            "Analise o CNPJ 50.000.002/0001-59 para 12 meses.",
            "analisar_risco",
            resultado="fora_do_escopo",
        ),
    ],
    "CNPJ inválido": [
        Step("Analise o CNPJ 00.000.000/0001-92.", "analisar_risco", status_ferramenta="erro"),
    ],
    "CNPJ inexistente": [
        Step("Analise o CNPJ 98.765.432/0001-98.", "analisar_risco", status_ferramenta="erro"),
    ],
    "acompanhamento sem empresa": [
        Step("E se fossem 12 meses?", None, status_ferramenta=None),
    ],
    "fora do escopo": [
        Step("Qual a previsão do dólar para amanhã?", None, status_ferramenta=None),
    ],
}


@pytest.fixture(scope="module")
def service() -> Any:
    try:
        llm_settings = LLMSettings()
    except ValidationError:
        pytest.skip("LLM_MODEL e LLM_API_KEY não configurados")
    settings = get_settings()
    if not (settings.artifacts_dir / "model" / "model.txt").exists():
        pytest.skip("modelo não treinado; rode `python -m consig_risk.modeling.train`")

    from google.adk.sessions import InMemorySessionService

    from consig_risk.agent.agent import build_agent, build_llm
    from consig_risk.agent.service import AgentService
    from consig_risk.modeling.artifact import RiskModel
    from consig_risk.risk.assessment import RiskAssessor
    from consig_risk.risk.policy import RiskPolicy
    from consig_risk.risk.repository import ParquetCompanyRepository

    assessor = RiskAssessor(
        RiskModel.load(settings.artifacts_dir / "model"),
        ParquetCompanyRepository(settings.data_dir / "processed" / "companies.parquet"),
        RiskPolicy(),
    )
    return AgentService(build_agent(build_llm(llm_settings), assessor), InMemorySessionService())


@pytest.mark.parametrize("nome", list(CONVERSAS))
def test_conversation(service: Any, nome: str) -> None:
    async def send(session: str, message: str) -> Any:
        # Contas gratuitas limitam requisições por minuto (ex.: 5 no Gemini Flash gratuito, e cada
        # mensagem faz duas chamadas): espera e tenta de novo em vez de falhar o caso.
        for _ in range(RATE_LIMIT_RETRIES):
            try:
                return await service.send("avaliacao", session, message)
            except AgentError as exc:
                if "cota diária" in str(exc):
                    pytest.skip(str(exc))  # esperar não resolve: a cota só volta no dia seguinte
                if "limitando" not in str(exc):
                    raise
                await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
        return await service.send("avaliacao", session, message)

    async def run() -> list[Any]:
        session = await service.create_session("avaliacao")
        return [await send(session, s.mensagem) for s in CONVERSAS[nome]]

    replies = asyncio.run(run())

    for step, reply in zip(CONVERSAS[nome], replies, strict=True):
        context = f"[{step.mensagem}] → {reply.texto[:300]}"
        assert reply.texto, context
        assert reply.numeros_nao_verificados == [], context
        if step.ferramenta is None:
            assert all(c.nome != "analisar_risco" or c.status == "erro" for c in reply.ferramentas)
            continue
        calls = [c for c in reply.ferramentas if c.nome == step.ferramenta]
        assert calls, f"{context} · ferramentas: {reply.ferramentas}"
        call = calls[-1]
        for key, value in step.argumentos.items():
            assert call.argumentos.get(key) == value, f"{context} · {call.argumentos}"
        assert call.status == step.status_ferramenta, context
        if step.resultado:
            assert reply.relatorio is not None and reply.relatorio["resultado"] == step.resultado
        if step.conceder is not None:
            assert reply.relatorio is not None and reply.relatorio["veredito"] is not None
            assert reply.relatorio["veredito"]["conceder"] is step.conceder, context
