"""API HTTP do sistema. A UI fala só com esta API.

Rotas:
    GET  /health                      estado da API, dos dados, do modelo e do LLM
    GET  /empresas/{cnpj}/risco        relatório de risco direto, sem LLM
    POST /conversas                   cria uma conversa com o agent
    GET  /conversas/{id}              histórico da conversa
    POST /conversas/{id}/mensagens    envia uma mensagem e recebe a resposta do agent
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from consig_risk import __version__
from consig_risk.agent.service import AgentError, AgentService, SessionNotFoundError
from consig_risk.config import LLMSettings, Settings, get_settings
from consig_risk.modeling.artifact import RiskModel
from consig_risk.risk.assessment import CompanyNotFoundError, RiskAssessor
from consig_risk.risk.cnpj import InvalidCNPJError
from consig_risk.risk.policy import RiskPolicy
from consig_risk.risk.repository import ParquetCompanyRepository

logger = logging.getLogger(__name__)

DEFAULT_USER = "analista"
UserId = Annotated[str, Header(alias="X-User-Id", max_length=64)]


@dataclass
class Runtime:
    """Dependências carregadas uma vez por processo (modelo, dados e agent)."""

    assessor: RiskAssessor
    agent: AgentService | None  # None quando o LLM não está configurado
    llm_model: str | None


def build_runtime(settings: Settings) -> Runtime:
    # Imports do ADK/LiteLLM só aqui: as rotas sem LLM não pagam o custo de importá-los.
    from google.adk.sessions import DatabaseSessionService

    from consig_risk.agent.agent import build_agent, build_llm

    model = RiskModel.load(settings.artifacts_dir / "model")
    repository = ParquetCompanyRepository(settings.data_dir / "processed" / "companies.parquet")
    assessor = RiskAssessor(model, repository, RiskPolicy())
    try:
        llm_settings = LLMSettings()
    except ValidationError:
        logger.warning("LLM_MODEL/LLM_API_KEY não configurados: o agent fica indisponível")
        return Runtime(assessor, None, None)
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    sessions = DatabaseSessionService(db_url=settings.session_db_url)
    agent = AgentService(build_agent(build_llm(llm_settings), assessor), sessions)
    return Runtime(assessor, agent, llm_settings.model)


class MessageIn(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = runtime or build_runtime(get_settings())
        yield

    app = FastAPI(title="Consig Risk API", version=__version__, lifespan=lifespan)

    def get_runtime(request: Request) -> Runtime:
        rt: Runtime = request.app.state.runtime
        return rt

    def get_agent(request: Request) -> AgentService:
        agent = get_runtime(request).agent
        if agent is None:
            raise HTTPException(
                503,
                "O agent está indisponível: configure LLM_MODEL e LLM_API_KEY no .env e "
                "reinicie a API.",
            )
        return agent

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        rt = get_runtime(request)
        return {
            "status": "ok",
            "version": __version__,
            "dados": asdict(rt.assessor.repository.snapshot),
            "modelo": rt.assessor.model.metadata.get("modelo"),
            "llm": {"configurado": rt.agent is not None, "modelo": rt.llm_model},
        }

    @app.get("/empresas/{cnpj:path}/risco")
    def company_risk(
        request: Request,
        cnpj: str,
        prazo_meses: Annotated[int | None, Query(gt=0, le=600)] = None,
    ) -> dict[str, Any]:
        try:
            return get_runtime(request).assessor.assess(cnpj, prazo_meses).to_dict()
        except InvalidCNPJError as exc:
            raise HTTPException(422, str(exc)) from exc
        except CompanyNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/conversas", status_code=201)
    async def create_conversation(
        request: Request, user_id: UserId = DEFAULT_USER
    ) -> dict[str, str]:
        return {"conversa_id": await get_agent(request).create_session(user_id)}

    @app.get("/conversas/{conversa_id}")
    async def conversation_history(
        request: Request, conversa_id: str, user_id: UserId = DEFAULT_USER
    ) -> dict[str, Any]:
        try:
            messages = await get_agent(request).history(user_id, conversa_id)
        except SessionNotFoundError as exc:
            raise HTTPException(404, "Conversa não encontrada.") from exc
        return {"conversa_id": conversa_id, "mensagens": [asdict(m) for m in messages]}

    @app.post("/conversas/{conversa_id}/mensagens")
    async def send_message(
        request: Request, conversa_id: str, body: MessageIn, user_id: UserId = DEFAULT_USER
    ) -> dict[str, Any]:
        try:
            reply = await get_agent(request).send(user_id, conversa_id, body.texto)
        except SessionNotFoundError as exc:
            raise HTTPException(404, "Conversa não encontrada.") from exc
        except AgentError as exc:
            raise HTTPException(502, str(exc)) from exc
        return asdict(reply)

    return app
