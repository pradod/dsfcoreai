"""Montagem do agent ADK com o modelo de LLM configurado por variáveis de ambiente (LiteLLM)."""

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from consig_risk.agent.prompts import INSTRUCTION
from consig_risk.agent.tools import make_tools
from consig_risk.config import LLMSettings
from consig_risk.risk.assessment import RiskAssessor

AGENT_NAME = "analista_de_risco"


def build_llm(settings: LLMSettings) -> LiteLlm:
    """LLM de qualquer provedor suportado pelo LiteLLM (Gemini, Anthropic, OpenAI e outros)."""
    # Repete falhas transitórias do provedor (ex.: 503 "high demand" do Gemini em picos).
    return LiteLlm(**settings.litellm_kwargs(), num_retries=2)


def build_agent(model: BaseLlm, assessor: RiskAssessor) -> LlmAgent:
    return LlmAgent(
        name=AGENT_NAME,
        description="Analisa o risco do empregador para consignado privado a partir do CNPJ.",
        model=model,
        instruction=INSTRUCTION,
        tools=make_tools(assessor),
        # Temperatura zero: o agent narra números prontos, não há o que ganhar com variação.
        generate_content_config=types.GenerateContentConfig(temperature=0),
    )
