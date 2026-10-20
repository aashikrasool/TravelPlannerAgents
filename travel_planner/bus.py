"""Peer-to-peer message bus between agents.

An agent asks another agent for something by calling `request(...)` (or `notice(...)` for one-way
information) and returning the message under the `mailbox` key. The graph's `deliver` node then
hands each pending message to the receiving agent's registered handler. Handlers answer with a
`reply`, which is delivered back to the asking agent's reply handler, if it has one.

    Research --prefer_area-->            Booking --lodging_reranked--> Budget
    Weather  --need_indoor_activities--> Research --reply--> Weather --new_candidates--> Budget
    Budget   --find_cheaper_lodging-->   Booking  --reply--> Budget
    Budget   --flag_expensive-->         Research --reply--> Budget
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .state import message

MAX_HOPS = 25

# (receiver, task) -> handler(state, msg) -> dict(updates=..., result=..., text=..., status=..., followups=[...])
_HANDLERS: dict[tuple[str, str], Callable] = {}
# (original sender, task) -> handler(state, reply) -> dict(updates=..., followups=[...])
_REPLY_HANDLERS: dict[tuple[str, str], Callable] = {}


def handles(receiver: str, task: str):
    def deco(fn: Callable) -> Callable:
        _HANDLERS[(receiver, task)] = fn
        return fn
    return deco


def on_reply(sender: str, task: str):
    def deco(fn: Callable) -> Callable:
        _REPLY_HANDLERS[(sender, task)] = fn
        return fn
    return deco


def request(sender: str, receiver: str, task: str, input: Any, text: str) -> dict:
    return message(sender, receiver, task, input=input, status="sent", kind="request", text=text)


def notice(sender: str, receiver: str, task: str, input: Any, text: str) -> dict:
    return message(sender, receiver, task, input=input, status="sent", kind="notice", text=text)


def already_asked(state: dict, sender: str, receiver: str, task: str) -> bool:
    return any(m["sender"] == sender and m["receiver"] == receiver and m["task"] == task
               for m in state.get("mailbox") or [])


def pending(state: dict) -> list[dict]:
    return [m for m in state.get("mailbox") or [] if m["status"] == "sent"]


def has_pending(state: dict) -> bool:
    return bool(pending(state)) and state.get("hops", 0) < MAX_HOPS


def _apply(working: dict, updates: dict) -> None:
    for k, v in updates.items():
        working[k] = v


def deliver(state: dict) -> dict:
    """Graph node: deliver the oldest pending peer message and run the receiver's handler."""
    msg = pending(state)[0]
    delivered = dict(msg, status="delivered")
    out: dict = {"mailbox": [delivered], "messages": [], "hops": state.get("hops", 0) + 1}
    followups: list[dict] = []

    if msg["kind"] in ("request", "notice"):
        handler = _HANDLERS.get((msg["receiver"], msg["task"]))
        if handler is None:
            res: dict = {"result": None, "text": f"{msg['receiver']} cannot handle '{msg['task']}'",
                         "status": "failed"}
        else:
            res = handler(state, msg) or {}
        out.update(res.get("updates", {}))
        followups += res.get("followups", [])
        if True:   # requests get a reply, notices an acknowledgement (both logged)
            has_reply_handler = msg["kind"] == "request" and (msg["sender"], msg["task"]) in _REPLY_HANDLERS
            reply = message(msg["receiver"], msg["sender"], msg["task"], input=None, result=res.get("result"),
                            status="sent" if has_reply_handler else "delivered",
                            kind="reply" if msg["kind"] == "request" else "ack",
                            text=res.get("text", ""), reply_to=msg["id"], error=res.get("error"))
            if res.get("status") in ("failed", "degraded"):
                reply["error"] = reply["error"] or res.get("text")
            followups.append(reply)
    elif msg["kind"] == "reply":
        handler = _REPLY_HANDLERS.get((msg["receiver"], msg["task"]))
        if handler is not None:
            res = handler(state, msg) or {}
            out.update(res.get("updates", {}))
            followups += res.get("followups", [])

    out["mailbox"] += followups
    out["messages"] += followups
    return out


def route_after(state: dict, default: str) -> str:
    return "deliver" if has_pending(state) else default


def summarize(m: dict) -> str:
    """One-line human readable version of a message (for logs and the UI)."""
    if m.get("text"):
        return m["text"]
    return f"{m['task']}"


def get_handler(receiver: str, task: str) -> Optional[Callable]:
    return _HANDLERS.get((receiver, task))
