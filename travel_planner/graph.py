"""LangGraph workflow: supervisor + specialists over one shared state, with peer-to-peer messaging.

    memory_retrieve -> supervisor_plan -+-> research -> weather -+
                                        |                        +-> budget -> [deliver]* -> supervisor_merge
                                        +-> booking -------------+                                 |
                                                                                               validate
                                                       passed? --yes--> finalize -> memory_update   |
                                                          | no                                      |
                                    human_review (interrupt) -> replan -> [deliver]* -> supervisor_merge

[deliver]* = the message router: while any agent has an undelivered message for another agent
(e.g. Budget -> Booking "find cheaper rooms"), deliver it to the receiver, run its handler and route
the reply back. Only when every conversation is finished does the supervisor merge the results.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .agents.booking_agent import booking_agent
from .agents.budget import budget_agent
from .agents.memory_agent import memory_retrieve, memory_update
from .agents.research import research_agent
from .agents.supervisor import supervisor_finalize, supervisor_merge, supervisor_plan
from .agents.validator import human_review, replan, validate
from .agents.weather_agent import weather_agent
from .bus import deliver, route_after
from .config import settings
from .state import PlanState


def _after_plan(state: PlanState):
    if state.get("status") == "failed":
        return END
    return ["research", "booking"]          # run in parallel


def _after_validate(state: PlanState) -> str:
    if state["validation"]["passed"]:
        return "finalize"
    if state.get("iterations", 0) >= settings.max_replans:
        return "finalize"                   # give up gracefully; plan is returned with its issues
    return "human_review"


def _to_merge(state: PlanState) -> str:
    return route_after(state, "supervisor_merge")


def build_graph(checkpointer=None):
    g = StateGraph(PlanState)
    g.add_node("memory_retrieve", memory_retrieve)
    g.add_node("supervisor_plan", supervisor_plan)
    g.add_node("research", research_agent)
    g.add_node("booking", booking_agent)
    g.add_node("weather", weather_agent)
    g.add_node("budget", budget_agent)
    g.add_node("supervisor_merge", supervisor_merge)
    g.add_node("deliver", deliver)
    g.add_node("validate", validate)
    g.add_node("human_review", human_review)
    g.add_node("replan", replan)
    g.add_node("finalize", supervisor_finalize)
    g.add_node("memory_update", memory_update)

    g.add_edge(START, "memory_retrieve")
    g.add_edge("memory_retrieve", "supervisor_plan")
    g.add_conditional_edges("supervisor_plan", _after_plan, ["research", "booking", END])
    g.add_edge("research", "weather")                 # weather re-ranks the research candidates
    g.add_edge(["weather", "booking"], "budget")      # budget waits for both branches
    g.add_conditional_edges("budget", _to_merge, ["deliver", "supervisor_merge"])
    g.add_conditional_edges("deliver", _to_merge, ["deliver", "supervisor_merge"])
    g.add_edge("supervisor_merge", "validate")
    g.add_conditional_edges("validate", _after_validate, ["finalize", "human_review"])
    g.add_edge("human_review", "replan")
    g.add_conditional_edges("replan", _to_merge, ["deliver", "supervisor_merge"])
    g.add_edge("finalize", "memory_update")
    g.add_edge("memory_update", END)
    return g.compile(checkpointer=checkpointer)
