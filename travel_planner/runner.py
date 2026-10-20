"""High-level API used by the CLI, the Streamlit app and the evaluation harness."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .graph import build_graph


@dataclass
class PlanResult:
    thread_id: str
    status: str                      # needs_input | done | failed
    state: dict
    question: Optional[dict] = None  # set when the planner needs the user to choose an option
    extra: dict = field(default_factory=dict)

    @property
    def itinerary(self) -> dict | None:
        return self.state.get("itinerary")


class TravelPlanner:
    def __init__(self, checkpointer: Any = None):
        self.graph = build_graph(checkpointer or InMemorySaver())

    def _result(self, thread_id: str, out: dict) -> PlanResult:
        interrupts = out.get("__interrupt__") or []
        state = {k: v for k, v in out.items() if k != "__interrupt__"}
        if interrupts:
            return PlanResult(thread_id, "needs_input", state, question=interrupts[0].value)
        return PlanResult(thread_id, state.get("status", "done"), state)

    def start(self, request: str = "", user_id: str = "default", auto: bool = False,
              inject_failures: Optional[dict] = None, thread_id: Optional[str] = None,
              form: Optional[dict] = None) -> PlanResult:
        """Plan from free text (`request`) or from the structured dropdown `form`."""
        thread_id = thread_id or uuid.uuid4().hex
        init = {"request": request, "form": form or {}, "user_id": user_id, "auto_mode": auto,
                "inject_failures": inject_failures or {}, "constraints": {}, "messages": [], "mailbox": [],
                "decisions": [], "hops": 0}
        out = self.graph.invoke(init, {"configurable": {"thread_id": thread_id}, "recursion_limit": 400})
        return self._result(thread_id, out)

    def resume(self, thread_id: str, choice_id: str) -> PlanResult:
        out = self.graph.invoke(Command(resume=choice_id),
                                {"configurable": {"thread_id": thread_id}, "recursion_limit": 400})
        return self._result(thread_id, out)


def plan_trip(request: str = "", user_id: str = "default", inject_failures: Optional[dict] = None,
              form: Optional[dict] = None) -> PlanResult:
    """One-shot helper: resolves any question automatically."""
    return TravelPlanner().start(request, user_id=user_id, auto=True, inject_failures=inject_failures, form=form)
