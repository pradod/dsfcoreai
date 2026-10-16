"""Execução do agent para a API: sessões, envio de mensagens e verificação dos números.

A API fica sem estado: a conversa (incluindo o CNPJ em análise) vive no session store do ADK.
Hoje é um SQLite; em produção, a mesma classe aceita a URL de um Postgres, e várias réplicas da API
podem atender a mesma conversa.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.genai import types

from consig_risk.agent.grounding import ungrounded_numbers

logger = logging.getLogger(__name__)

APP_NAME = "consig_risk"


class AgentError(RuntimeError):
    """Falha ao gerar a resposta (provedor de LLM indisponível, chave inválida etc.)."""


class SessionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ToolCall:
    nome: str
    argumentos: dict[str, Any]
    status: str | None


@dataclass(frozen=True)
class AgentReply:
    texto: str
    relatorio: dict[str, Any] | None  # último relatório de risco bem-sucedido deste turno
    ferramentas: list[ToolCall] = field(default_factory=list)
    numeros_nao_verificados: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Message:
    papel: str  # "analista" | "agent"
    texto: str


class AgentService:
    def __init__(self, agent: BaseAgent, session_service: BaseSessionService) -> None:
        self._sessions = session_service
        self._runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    async def create_session(self, user_id: str) -> str:
        session = await self._sessions.create_session(app_name=APP_NAME, user_id=user_id)
        return session.id

    async def _events(self, user_id: str, session_id: str) -> list[Event]:
        session = await self._sessions.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if session is None:
            raise SessionNotFoundError(session_id)
        return list(session.events)

    async def history(self, user_id: str, session_id: str) -> list[Message]:
        messages = []
        for event in await self._events(user_id, session_id):
            text = _text(event)
            if not text:
                continue
            messages.append(Message("analista" if event.author == "user" else "agent", text))
        return messages

    async def send(self, user_id: str, session_id: str, text: str) -> AgentReply:
        await self._events(user_id, session_id)  # 404 antes de gastar uma chamada ao LLM
        message = types.Content(role="user", parts=[types.Part(text=text)])
        calls: list[ToolCall] = []
        args_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        report: dict[str, Any] | None = None
        final_text = ""
        try:
            async for event in self._runner.run_async(
                user_id=user_id, session_id=session_id, new_message=message
            ):
                for fc in event.get_function_calls():
                    args_by_id[fc.id or fc.name or ""] = (fc.name or "", dict(fc.args or {}))
                for fr in event.get_function_responses():
                    response = dict(fr.response or {})
                    name, args = args_by_id.get(fr.id or fr.name or "", (fr.name or "", {}))
                    calls.append(ToolCall(name, args, response.get("status")))
                    if name == "analisar_risco" and response.get("status") == "ok":
                        report = response
                if event.is_final_response() and _text(event):
                    final_text = _text(event)
        except Exception as exc:
            logger.exception("falha ao executar o agent")
            raise AgentError(_friendly_error(exc)) from exc

        if not final_text:
            raise AgentError("O modelo de linguagem não devolveu resposta. Tente novamente.")
        events = await self._events(user_id, session_id)
        return AgentReply(
            texto=final_text,
            relatorio=report,
            ferramentas=calls,
            numeros_nao_verificados=ungrounded_numbers(
                final_text, _tool_outputs(events), _user_messages(events)
            ),
        )


def _text(event: Event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(p.text for p in event.content.parts if p.text and not p.thought).strip()


def _tool_outputs(events: list[Event]) -> list[dict[str, Any]]:
    return [dict(fr.response or {}) for e in events for fr in e.get_function_responses()]


def _user_messages(events: list[Event]) -> list[str]:
    return [_text(e) for e in events if e.author == "user"]


def _exception_names(exc: BaseException) -> str:
    """Nomes de toda a cadeia de exceções: o ADK embrulha os erros do LiteLLM nas suas próprias."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return " ".join(names)


def _exception_text(exc: BaseException) -> str:
    texts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(texts) < 10:
        texts.append(str(current))
        current = current.__cause__ or current.__context__
    return " ".join(texts)


def _friendly_error(exc: Exception) -> str:
    name = _exception_names(exc)
    if "Authentication" in name or "PermissionDenied" in name:
        return "O provedor de LLM recusou a chave. Confira LLM_API_KEY e LLM_MODEL no .env."
    if "RateLimit" in name:
        if "PerDay" in _exception_text(exc):
            return (
                "A cota diária do provedor de LLM para este modelo acabou. Troque LLM_MODEL por "
                "outro modelo ou aguarde a renovação da cota."
            )
        return "O provedor de LLM está limitando as requisições. Aguarde um pouco e tente de novo."
    if "NotFound" in name or "BadRequest" in name:
        return (
            "O provedor de LLM não aceitou o pedido. Confira se LLM_MODEL é um modelo válido "
            f"e com suporte a ferramentas ({type(exc).__name__})."
        )
    if "Timeout" in name or "Connection" in name or "ServiceUnavailable" in name:
        return "Não foi possível falar com o provedor de LLM. Tente novamente em instantes."
    return f"Falha ao gerar a resposta ({type(exc).__name__}). Tente novamente."
