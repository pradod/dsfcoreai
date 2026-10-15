import pytest
from pydantic import ValidationError

from consig_risk.config import LLMSettings


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_MODEL", "LLM_API_KEY", "LLM_API_BASE"):
        monkeypatch.delenv(var, raising=False)


def test_llm_settings_reads_required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-2.5-flash")
    monkeypatch.setenv("LLM_API_KEY", "secret")

    settings = LLMSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.litellm_kwargs() == {"model": "gemini/gemini-2.5-flash", "api_key": "secret"}


def test_llm_settings_includes_api_base_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_API_BASE", "http://proxy.local:4000")

    settings = LLMSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.litellm_kwargs()["api_base"] == "http://proxy.local:4000"


def test_llm_settings_fails_fast_without_required_vars() -> None:
    with pytest.raises(ValidationError):
        LLMSettings(_env_file=None)  # type: ignore[call-arg]


def test_llm_api_key_is_not_leaked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("LLM_API_KEY", "super-secret")

    settings = LLMSettings(_env_file=None)  # type: ignore[call-arg]

    assert "super-secret" not in repr(settings)
