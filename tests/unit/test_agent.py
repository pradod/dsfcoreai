"""Fluxo completo do agent (ADK) com um LLM roteirizado (`ScriptedLlm`) no lugar do provedor.

Testa a integração (ferramentas, estado da sessão, perguntas de acompanhamento, erros e checagem
de números) sem custo nem variação de LLM.
"""

import asyncio
from typing import Any

import pytest
from google.adk.sessions import InMemorySessionService

from consig_risk.agent.agent import build_agent
from consig_risk.agent.grounding import ungrounded_numbers
from consig_risk.agent.service import AgentError, AgentService, SessionNotFoundError
from consig_risk.agent.tools import STATE_CNPJ
from tests.factories import RISKY, SAFE, ScriptedLlm, build_test_assessor


@pytest.fixture(scope="module")
def assessor() -> Any:
    return build_test_assessor()


def _service(assessor: Any, **llm: Any) -> tuple[AgentService, InMemorySessionService]:
    sessions = InMemorySessionService()
    return AgentService(build_agent(ScriptedLlm(**llm), assessor), sessions), sessions


def test_request_with_term_calls_tool_and_returns_grounded_report(assessor: Any) -> None:
    service, _ = _service(assessor)

    async def run() -> Any:
        session = await service.create_session("analista")
        return await service.send(
            "analista", session, "Analise o CNPJ 00.000.000/0001-91 para um consignado de 12 meses."
        )

    reply = asyncio.run(run())

    assert [c.nome for c in reply.ferramentas] == ["analisar_risco"]
    assert reply.ferramentas[0].argumentos == {"cnpj": "00.000.000/0001-91", "prazo_meses": 12}
    assert reply.relatorio is not None and reply.relatorio["veredito"]["conceder"]
    assert "Conceder" in reply.texto
    assert reply.numeros_nao_verificados == []


def test_follow_up_reuses_the_company_without_repeating_the_cnpj(assessor: Any) -> None:
    service, sessions = _service(assessor)

    async def run() -> tuple[Any, Any, Any]:
        session = await service.create_session("analista")
        first = await service.send(
            "analista", session, f"Faça o relatório de risco do CNPJ {RISKY}"
        )
        second = await service.send("analista", session, "E se fossem 6 meses?")
        stored = await sessions.get_session(
            app_name="consig_risk", user_id="analista", session_id=session
        )
        return first, second, stored

    first, second, stored = asyncio.run(run())

    assert first.relatorio["veredito"] is None  # pedido sem prazo: prazo máximo
    assert second.ferramentas[0].argumentos == {"prazo_meses": 6}
    assert second.relatorio["empresa"]["cnpj"] == first.relatorio["empresa"]["cnpj"]
    assert second.relatorio["prazo_pedido_meses"] == 6
    assert stored.state[STATE_CNPJ] == "11.222.333/0001-81"


def test_invalid_cnpj_is_explained_instead_of_answered(assessor: Any) -> None:
    service, _ = _service(assessor)

    async def run() -> Any:
        session = await service.create_session("analista")
        return await service.send("analista", session, "Analise o CNPJ 00.000.000/0001-92")

    reply = asyncio.run(run())

    assert reply.ferramentas[0].status == "erro"
    assert reply.relatorio is None
    assert "CNPJ inválido" in reply.texto


def test_follow_up_without_any_company_asks_for_the_cnpj(assessor: Any) -> None:
    service, _ = _service(assessor)

    async def run() -> Any:
        session = await service.create_session("analista")
        return await service.send("analista", session, "E se fossem 12 meses?")

    reply = asyncio.run(run())

    assert "Nenhuma empresa em análise" in reply.texto


def test_policy_question_uses_the_policy_tool(assessor: Any) -> None:
    service, _ = _service(assessor)

    async def run() -> Any:
        session = await service.create_session("analista")
        return await service.send("analista", session, "Qual é o critério de risco aceitável?")

    reply = asyncio.run(run())

    assert [c.nome for c in reply.ferramentas] == ["explicar_politica"]
    assert "10%" in reply.texto
    assert reply.numeros_nao_verificados == []


def test_invented_numbers_are_flagged(assessor: Any) -> None:
    service, _ = _service(assessor, invent=True)

    async def run() -> Any:
        session = await service.create_session("analista")
        return await service.send("analista", session, f"Analise o CNPJ {SAFE} para 12 meses")

    reply = asyncio.run(run())

    assert reply.numeros_nao_verificados == ["99", "37,7"]


def test_history_keeps_the_conversation_in_order(assessor: Any) -> None:
    service, _ = _service(assessor)

    async def run() -> Any:
        session = await service.create_session("analista")
        await service.send("analista", session, f"Analise o CNPJ {SAFE} para 12 meses")
        return await service.history("analista", session)

    history = asyncio.run(run())

    assert [m.papel for m in history] == ["analista", "agent"]
    assert history[0].texto.startswith("Analise o CNPJ")


def test_unknown_session_and_provider_errors_are_reported_clearly(assessor: Any) -> None:
    service, _ = _service(assessor)
    failing, _ = _service(assessor, fail=True)

    async def unknown() -> Any:
        return await service.send("analista", "nao-existe", "oi")

    async def provider_down() -> Any:
        session = await failing.create_session("analista")
        return await failing.send("analista", session, f"Analise o CNPJ {SAFE}")

    with pytest.raises(SessionNotFoundError):
        asyncio.run(unknown())
    with pytest.raises(AgentError, match="LLM_API_KEY"):
        asyncio.run(provider_down())


@pytest.mark.parametrize(
    ("response", "sources", "expected"),
    [
        ("Risco de 2,0% em 24 meses.", [{"risco_pct": 2.0, "prazo_meses": 24}], []),
        ("Risco de 2,04%.", [{"risco_pct": 2.0}], []),  # arredondamento tolerado
        ("Risco de 3,5%.", [{"risco_pct": 2.0}], ["3,5"]),
        ("5.427 filiais", [{"filiais_ativas": 5427}], []),
        ("CNPJ 00.000.000/0001-91", [{"cnpj": "00.000.000/0001-91"}], []),
        ("Limite de 10%.", [{"limite_risco_pct": 10.0}], []),
        ("1. Primeiro fator\n2. Segundo fator", [{}], []),  # marcadores de lista
        ("os 3 principais fatores", [{}], []),  # contagem pequena sem unidade
        ("prazo de 3 meses", [{}], ["3"]),  # com unidade, precisa ter origem
        ("multiplicador ×1,22", [{"multiplicador": 1.22}], []),
        # Formatos que o LLM real usou: milhar com espaço e abreviação de grandeza.
        ("dívida de R$ 506 691 912,13", [{"valor": "R$ 506.691.912,13"}], []),
        ("capital de R$ 120 bi", [{"valor": "R$ 120.000.000.000,00"}], []),
        ("dívida de R$ 507 milhões", [{"valor": "R$ 506.691.912,13"}], []),
        ("capital de R$ 300 bi", [{"valor": "R$ 120.000.000.000,00"}], ["300"]),
    ],
)
def test_ungrounded_numbers(response: str, sources: list[Any], expected: list[str]) -> None:
    assert ungrounded_numbers(response, sources) == expected


def test_numbers_from_the_analyst_message_are_grounded() -> None:
    assert ungrounded_numbers("Para 48 meses, não conceder.", [{}], ["e se fossem 48 meses?"]) == []
