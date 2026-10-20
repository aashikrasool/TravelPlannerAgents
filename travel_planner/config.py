"""Central configuration. Every setting can be overridden with an environment variable
(or a `.env` file in the project root)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # --- LLM (open-source models) -------------------------------------------------
    # "ollama"  -> local Ollama server (default, easiest)
    # "openai"  -> any OpenAI-compatible server hosting an open model (vLLM, LM Studio,
    #              llama.cpp server, Groq, Together...)
    # "none"    -> no LLM; agents use deterministic rule-based logic (offline / tests)
    # "auto"    -> try Ollama, fall back to "none" if it is not running
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "qwen2.5:7b"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "http://localhost:8000/v1"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", "not-needed"))
    llm_timeout: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT", "120")))
    llm_temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.2")))

    # --- External tools ----------------------------------------------------------
    # "auto" tries the live free API and falls back to the offline source on failure,
    # "live" uses only the live API, "mock" never touches the network (reproducible).
    weather_mode: str = field(default_factory=lambda: _env("WEATHER_MODE", "auto"))
    places_mode: str = field(default_factory=lambda: _env("PLACES_MODE", "auto"))
    http_timeout: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT", "8")))
    tool_retries: int = field(default_factory=lambda: int(_env("TOOL_RETRIES", "1")))
    fx_mode: str = field(default_factory=lambda: _env("FX_MODE", "auto"))   # auto = live rates if reachable

    # --- Planner -------------------------------------------------------------------
    memory_db: str = field(default_factory=lambda: _env("MEMORY_DB", str(ROOT / "data" / "memory.sqlite")))
    max_replans: int = field(default_factory=lambda: int(_env("MAX_REPLANS", "8")))
    default_currency: str = field(default_factory=lambda: _env("DEFAULT_CURRENCY", "KRW"))


settings = Settings()
if settings.memory_db != ":memory:" and not Path(settings.memory_db).is_absolute():
    settings.memory_db = str(ROOT / settings.memory_db)

# Fixed conversion rates to KRW (approximate; good enough for budget checks).
FX_TO_KRW = {"KRW": 1.0, "USD": 1380.0, "EUR": 1500.0, "JPY": 9.2, "GBP": 1750.0, "CNY": 190.0}
