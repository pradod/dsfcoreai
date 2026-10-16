from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from consig_risk import __version__
from consig_risk.agent.agent import build_agent
from consig_risk.agent.service import AgentService
from consig_risk.api.main import Runtime, create_app
from consig_risk.risk.assessment import RiskAssessor
from tests.factories import SAFE, ScriptedLlm, build_test_assessor


@pytest.fixture(scope="module")
def assessor() -> RiskAssessor:
    return build_test_assessor()


def _client(runtime: Runtime) -> TestClient:
    return TestClient(create_app(runtime))


@pytest.fixture
def client(assessor: RiskAssessor) -> Iterator[TestClient]:
    agent = AgentService(build_agent(ScriptedLlm(), assessor), InMemorySessionService())
    with _client(Runtime(assessor, agent, "scripted")) as c:
        yield c


def test_health_reports_data_model_and_llm(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok" and body["version"] == __version__
    assert body["dados"]["data_referencia"] == "2026-10-11"
    assert body["llm"] == {"configurado": True, "modelo": "scripted"}


def test_risk_endpoint_returns_report_without_llm(client: TestClient) -> None:
    response = client.get("/empresas/00.000.000/0001-91/risco", params={"prazo_meses": 12})

    assert response.status_code == 200
    assert response.json()["veredito"]["conceder"] is True


@pytest.mark.parametrize(
    ("cnpj", "status"), [("00.000.000/0001-92", 422), ("98.765.432/0001-98", 404)]
)
def test_risk_endpoint_errors(client: TestClient, cnpj: str, status: int) -> None:
    response = client.get(f"/empresas/{cnpj}/risco")

    assert response.status_code == status
    assert response.json()["detail"]


def test_conversation_flow(client: TestClient) -> None:
    conversa = client.post("/conversas").json()["conversa_id"]

    reply = client.post(
        f"/conversas/{conversa}/mensagens", json={"texto": f"Analise o CNPJ {SAFE} para 12 meses"}
    )
    history = client.get(f"/conversas/{conversa}").json()["mensagens"]

    assert reply.status_code == 200
    body = reply.json()
    assert body["relatorio"]["resultado"] == "avaliada"
    assert body["ferramentas"][0]["nome"] == "analisar_risco"
    assert body["numeros_nao_verificados"] == []
    assert [m["papel"] for m in history] == ["analista", "agent"]


def test_unknown_conversation_and_empty_message(client: TestClient) -> None:
    assert client.post("/conversas/nao-existe/mensagens", json={"texto": "oi"}).status_code == 404
    conversa = client.post("/conversas").json()["conversa_id"]
    assert client.post(f"/conversas/{conversa}/mensagens", json={"texto": ""}).status_code == 422


def test_provider_failure_becomes_bad_gateway_with_clear_message(assessor: RiskAssessor) -> None:
    agent = AgentService(build_agent(ScriptedLlm(fail=True), assessor), InMemorySessionService())
    with _client(Runtime(assessor, agent, "scripted")) as client:
        conversa = client.post("/conversas").json()["conversa_id"]
        response = client.post(f"/conversas/{conversa}/mensagens", json={"texto": "oi"})

    assert response.status_code == 502
    assert "LLM_API_KEY" in response.json()["detail"]


def test_without_llm_the_agent_routes_explain_how_to_configure(assessor: RiskAssessor) -> None:
    with _client(Runtime(assessor, None, None)) as client:
        assert client.get("/health").json()["llm"]["configurado"] is False
        response = client.post("/conversas")
        assert client.get(f"/empresas/{SAFE}/risco").status_code == 200

    assert response.status_code == 503
    assert "LLM_MODEL" in response.json()["detail"]
