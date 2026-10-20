"""Thin client for open-source LLMs.

Supports a local Ollama server (default) or any OpenAI-compatible endpoint that serves an
open model (vLLM, LM Studio, llama.cpp server, ...). When no model is reachable, `get_llm()`
returns ``None`` and every agent falls back to deterministic rule-based logic, so the whole
system still runs end to end (that is also how the test-suite runs).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import requests

from .config import settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Minimal chat interface shared by all agents."""

    name = "base"

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:  # pragma: no cover
        raise NotImplementedError

    def chat_json(self, system: str, user: str) -> Any:
        """Ask for JSON and parse it robustly (models sometimes wrap it in prose/markdown)."""
        raw = self.chat(system + "\nRespond with valid JSON only.", user, json_mode=True)
        return extract_json(raw)


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, model: str, url: str, timeout: float, temperature: float):
        self.model, self.url, self.timeout, self.temperature = model, url.rstrip("/"), timeout, temperature

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=2)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            base = self.model.split(":")[0]
            if not any(m == self.model or m.startswith(base) for m in models):
                log.warning("Ollama is running but model %s is not pulled. Run: ollama pull %s",
                            self.model, self.model)
                return False
            return True
        except Exception:
            return False

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except Exception as exc:  # network, timeout, bad response
            raise LLMError(f"Ollama call failed: {exc}") from exc


class OpenAICompatClient(LLMClient):
    name = "openai-compatible"

    def __init__(self, model: str, base_url: str, api_key: str, timeout: float, temperature: float):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key, self.timeout, self.temperature = api_key, timeout, temperature

    def available(self) -> bool:
        """Check the key and the server once, so a wrong key shows up as 'rule-based' in the UI."""
        try:
            r = requests.get(f"{self.base_url}/models", timeout=8,
                             headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code in (401, 403):
                log.warning("LLM server rejected the API key (HTTP %s)", r.status_code)
                return False
            return r.status_code < 500
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM server not reachable at %s: %s", self.base_url, exc)
            return False

    @property
    def host(self) -> str:
        return self.base_url.split("//")[-1].split("/")[0]

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"OpenAI-compatible call failed: {exc}") from exc


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ```json fences and leading prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model did not return JSON: {text[:200]}")


_cached: dict[str, Optional[LLMClient]] = {}


def get_llm(provider: Optional[str] = None) -> Optional[LLMClient]:
    """Return the configured LLM client, or None when running without a model."""
    provider = (provider or settings.llm_provider).lower()
    if provider in _cached:
        return _cached[provider]
    client: Optional[LLMClient] = None
    if provider in ("ollama", "auto"):
        ollama = OllamaClient(settings.llm_model, settings.ollama_url, settings.llm_timeout,
                              settings.llm_temperature)
        if ollama.available():
            client = ollama
        elif provider == "ollama":
            log.warning("Ollama not reachable at %s; continuing with rule-based agents.", settings.ollama_url)
    elif provider == "openai":
        remote = OpenAICompatClient(settings.llm_model, settings.openai_base_url, settings.openai_api_key,
                                    settings.llm_timeout, settings.llm_temperature)
        client = remote if remote.available() else None
    _cached[provider] = client
    return client


def set_llm(client: Optional[LLMClient], provider: Optional[str] = None) -> None:
    """Override the client (used by tests and by the UI's model selector)."""
    _cached[(provider or settings.llm_provider).lower()] = client


def describe_llm() -> str:
    client = get_llm()
    if client is None:
        return "rule-based (no LLM connected)"
    where = f" via {client.host}" if hasattr(client, "host") else ""
    return f"{getattr(client, 'model', '?')} ({client.name}{where})"
