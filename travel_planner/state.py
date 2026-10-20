"""Shared plan state and the structured message format used between agents.

Every agent reads from and writes to one `PlanState`. Agents talk through `AgentMessage`
records (task · input · result · status · error). Two channels exist:

* `messages` - the complete, append-only log of everything that was said (for the UI / report);
* `mailbox`  - messages that still have to be *delivered*. Agents can address each other
  directly (peer-to-peer, e.g. Budget -> Booking "find cheaper rooms"); the graph's message
  router delivers each one to its receiver, whose handler acts on it and replies.
"""
from __future__ import annotations

import operator
import time
import uuid
from typing import Annotated, Any, Optional, TypedDict


def merge_dicts(left: Optional[dict], right: Optional[dict]) -> dict:
    """Reducer: lets agents running in parallel update different keys of one dict."""
    out = dict(left or {})
    out.update(right or {})
    return out


def merge_by_id(left: Optional[list], right: Optional[list]) -> list:
    """Reducer for the mailbox: new messages are appended, updated ones (same id) replaced."""
    out = list(left or [])
    index = {m["id"]: i for i, m in enumerate(out)}
    for m in right or []:
        if m["id"] in index:
            out[index[m["id"]]] = m
        else:
            index[m["id"]] = len(out)
            out.append(m)
    return out


class PlanState(TypedDict, total=False):
    # input
    request: str
    form: dict                            # structured input from the dropdown form (optional)
    user_id: str
    auto_mode: bool                       # resolve human-in-the-loop questions automatically (evaluation)
    inject_failures: Annotated[dict, merge_dicts]   # {"weather": 2, ...} -> tool fails that many calls

    # supervisor
    trip: dict                            # resolved request: destination, country, days, budget_krw, ...
    preferences: dict                     # {"likes": [...], "dislikes": [...]} from memory + request
    tasks: list                           # tasks the supervisor delegated

    # specialists
    candidates: list                      # research agent: places / restaurants
    weather: dict                         # weather agent: forecast + per-day indoor flags
    lodging_options: list                 # booking agent
    transport_options: list               # booking agent
    prices: dict                          # budget agent: price table + allocation
    selection: dict                       # chosen hotel / transport

    # integration + validation
    itinerary: dict
    validation: dict
    constraints: dict                     # updated by user decisions
    decisions: Annotated[list, operator.add]
    pending_question: dict
    iterations: int
    summary: str
    status: str                           # planning | needs_input | done | failed

    # communication
    messages: Annotated[list, operator.add]
    mailbox: Annotated[list, merge_by_id]
    hops: int                             # peer messages delivered so far (loop guard)
    tool_status: Annotated[dict, merge_dicts]


def message(sender: str, receiver: str, task: str, input: Any = None, result: Any = None,
            status: str = "ok", error: Optional[str] = None, kind: str = "report",
            text: str = "", reply_to: Optional[str] = None) -> dict:
    """Create one structured inter-agent message.

    kind: task (supervisor delegates) | report (agent reports back) | request (agent asks a peer) |
          reply (answer to a request) | notice (one-way information to a peer)
    """
    return {
        "id": uuid.uuid4().hex[:8],
        "ts": round(time.time(), 3),
        "sender": sender,
        "receiver": receiver,
        "kind": kind,
        "task": task,
        "text": text,
        "input": input,
        "result": result,
        "status": status,      # sent | delivered | ok | degraded | failed
        "error": error,
        "reply_to": reply_to,
    }
