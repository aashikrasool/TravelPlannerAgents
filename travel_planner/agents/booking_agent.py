"""Booking agent: gathers priced accommodation and transport options with availability."""
from __future__ import annotations

import datetime as dt

from .. import bus, world
from ..state import PlanState, message
from ..tools import booking as booking_tool
from ..tools.failures import FailureInjector, ToolError, call_with_retry


def booking_agent(state: PlanState) -> dict:
    trip = state["trip"]
    constraints = state.get("constraints", {}) or {}
    inj = FailureInjector(state.get("inject_failures"))
    start = dt.date.fromisoformat(trip["start_date"])
    nights = max(0, trip["days"] - 1)
    errors: list[str] = []

    lodging: list[dict] = []
    lodging_status = {"status": "ok", "errors": [], "source": "mock booking API"}
    if nights:
        try:
            lodging, errs = call_with_retry(
                "booking", lambda: booking_tool.search_lodging(trip, start, nights, trip["travelers"]), inj)
            lodging_status["status"] = "recovered" if errs else "ok"
            lodging_status["errors"] = errs
        except ToolError as exc:
            # fall back to list prices without live availability, and say so
            lodging = booking_tool.search_lodging(trip, start, nights, trip["travelers"])
            for h in lodging:
                h.update(available=True, source="estimated list price (booking API down)")
            lodging_status = {"status": "degraded", "errors": [str(exc)], "source": "estimated list prices"}
        errors += lodging_status["errors"]
    # keep any budget options found earlier through the Budget <-> Booking conversation
    lodging += [h for h in state.get("lodging_options") or [] if h["id"].startswith("low_")]
    # keep honouring an earlier "prefer_area" request from Research when re-running
    centre = next((m["input"] for m in state.get("mailbox") or []
                   if m["task"] == "prefer_area" and m["kind"] == "request"), None)
    if centre:
        for h in lodging:
            if h.get("lat") is not None:
                h["distance_km"] = round(world.distance_km(h, centre), 1)

    max_total = constraints.get("max_lodging_total")
    if max_total is not None:
        cheaper = [h for h in lodging if h["total"] <= max_total]
        lodging = cheaper or lodging

    transport_status = {"status": "ok", "errors": [], "source": "mock transport API"}
    try:
        transport, errs = call_with_retry("transport", lambda: booking_tool.search_transport(trip), inj)
        transport_status.update(status="recovered" if errs else "ok", errors=errs)
    except ToolError as exc:
        transport = booking_tool.search_transport(trip)
        for t in transport:
            t["source"] = "estimated fare (transport API down)"
        transport_status = {"status": "degraded", "errors": [str(exc)], "source": "estimated fares"}
    errors += transport_status["errors"]

    available = [h for h in lodging if h["available"]]
    degraded = "degraded" in (lodging_status["status"], transport_status["status"])
    text = (f"{len(available)}/{len(lodging)} hotels available for {nights} night(s)"
            + (f"; sold out: {', '.join(h['name'] for h in lodging if not h['available'])}"
               if len(available) < len(lodging) else "")
            + (f"; {len(transport)} transport option(s) from {trip['origin']}" if transport else "") + ".")
    return {
        "lodging_options": lodging,
        "transport_options": transport,
        "tool_status": {"booking": lodging_status, "transport": transport_status},
        "inject_failures": inj.remaining(),
        "messages": [message("booking", "supervisor", "gather_lodging_and_transport",
                             {"destination": trip["destination"], "origin": trip.get("origin"), "nights": nights},
                             {"lodging_options": len(lodging), "available": len(available),
                              "transport_options": [t["name"] for t in transport]},
                             "degraded" if degraded else "ok", "; ".join(errors) or None, text=text)],
    }


# ---------------------------------------------------------------- peer requests handled by Booking

@bus.handles("booking", "prefer_area")
def handle_prefer_area(state: dict, msg: dict) -> dict:
    """Research says where the activities are: rank hotels by distance, then tell Budget."""
    centre = msg["input"]
    ranked = []
    for h in state.get("lodging_options") or []:
        h = dict(h)
        if h.get("lat") is not None:
            h["distance_km"] = round(world.distance_km(h, centre), 1)
        ranked.append(h)
    ranked.sort(key=lambda h: h.get("distance_km", 99))
    near = next((h for h in ranked if h["available"]), None)
    text = (f"Ranked {len(ranked)} hotels by distance to {centre['area']}; nearest available is "
            f"{near['name']} ({near.get('distance_km', '?')} km).") if near else "No hotel has location data."
    follow = bus.notice("booking", "budget", "lodging_reranked", {"nearest": near and near["id"]},
                        "Hotel list is now ranked by distance to the activities - please re-check your pick.")
    return {"updates": {"lodging_options": ranked}, "result": {"nearest": near and near["name"]}, "text": text,
            "followups": [follow]}


@bus.handles("booking", "find_cheaper_lodging")
def handle_find_cheaper(state: dict, msg: dict) -> dict:
    """Budget asks for rooms under a price cap: search low-cost lodging types too."""
    trip = state["trip"]
    start = dt.date.fromisoformat(trip["start_date"])
    nights = max(0, trip["days"] - 1)
    inj = FailureInjector(state.get("inject_failures"))
    try:
        extra, _ = call_with_retry("booking", lambda: booking_tool.search_budget_lodging(
            trip, start, nights, trip["travelers"]), inj)
    except ToolError as exc:
        return {"updates": {"inject_failures": inj.remaining()}, "result": {"options": []},
                "text": f"Could not search budget lodging: {exc}", "status": "failed"}
    known = {h["id"] for h in state.get("lodging_options") or []}
    lodging = (state.get("lodging_options") or []) + [h for h in extra if h["id"] not in known]
    cap = msg["input"]["max_total"]
    fits = [h for h in extra if h["available"] and h["total"] <= cap]
    text = (f"Found {len(extra)} low-cost options; {len(fits)} fit under {world.money(cap, trip['currency'])}"
            + (f", e.g. {fits[0]['name']} at {world.money(fits[0]['total'], trip['currency'])}." if fits else "."))
    return {"updates": {"lodging_options": lodging, "inject_failures": inj.remaining()},
            "result": {"options": [h["name"] for h in extra], "fits": [h["name"] for h in fits]}, "text": text}
