"""Configuração lida exclusivamente de variáveis de ambiente (ou de um arquivo .env).

Nenhuma configuração exige mexer no código: o avaliador só preenche o .env.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração geral da aplicação (caminhos e endereços entre serviços)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    artifacts_dir: Path = Path("artifacts")
    api_url: str = "http://localhost:8000"
    # Onde o ADK guarda as conversas. SQLite basta para uma instância; para várias réplicas da
    # API, aponte para um Postgres (ex.: postgresql+asyncpg://...).
    session_db_url: str = "sqlite+aiosqlite:///data/sessions.db"
    # Teto de memória do DuckDB no preparo dos dados. Acima disso ele usa disco (mais lento, mas
    # não estoura a memória do Docker, que no Docker Desktop costuma ser 8 GB para tudo).
    duckdb_memory_limit: str = "4GB"


class LLMSettings(BaseSettings):
    """Configuração do provedor de LLM, conforme a seção "Configuração do LLM" do case.

    Fica separada de `Settings` porque só o agent precisa dela: os serviços de dados e
    treino rodam sem chave de LLM.
    """

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    model: str
    api_key: SecretStr
    api_base: str | None = None

    def litellm_kwargs(self) -> dict[str, str]:
        """Argumentos para `google.adk.models.lite_llm.LiteLlm`."""
        kwargs = {"model": self.model, "api_key": self.api_key.get_secret_value()}
        if self.api_base:
            kwargs["api_base"] = self.api_base
        return kwargs


@lru_cache
def get_settings() -> Settings:
    return Settings()
