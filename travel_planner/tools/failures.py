"""Failure injection + retry helper, used to test how the agents recover from API failures."""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..config import settings

log = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """Raised by any tool whose external API failed (real or injected)."""


class FailureInjector:
    """Wraps the `inject_failures` dict from the plan state, e.g. {"weather": 2}.

    Each call to `check(tool)` consumes one planned failure and raises `ToolError`.
    The remaining counts are written back to the state so failures persist across agents.
    """

    def __init__(self, plan: dict | None):
        self.plan = dict(plan or {})

    def check(self, tool: str) -> None:
        n = self.plan.get(tool, 0)
        if n and n > 0:
            self.plan[tool] = n - 1 if n < 99 else n   # 99 = permanent outage
            raise ToolError(f"[injected] {tool} API unavailable")

    def remaining(self) -> dict:
        return self.plan


def call_with_retry(tool: str, fn: Callable[[], Any], injector: FailureInjector,
                    retries: int | None = None) -> tuple[Any, list[str]]:
    """Call `fn` with retries. Returns (result, errors). Raises ToolError if every attempt fails."""
    retries = settings.tool_retries if retries is None else retries
    errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            injector.check(tool)
            return fn(), errors
        except Exception as exc:  # noqa: BLE001 - any API error counts as a tool failure
            errors.append(f"attempt {attempt + 1}: {exc}")
            log.info("tool %s failed (%s)", tool, exc)
    raise ToolError(f"{tool} failed after {retries + 1} attempts: {errors[-1]}")
