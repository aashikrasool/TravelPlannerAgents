"""Single-agent baseline for the evaluation.

One agent does everything in one sequential pass with the SAME tools and data as the
multi-agent system, so the comparison measures orchestration rather than tool access:

* tools are called one after another in a single context (no parallel branches);
* a tool failure that survives the retry stops the whole workflow (no isolated fallback);
* there is no separate validation + budget-check loop and no human-in-the-loop replanning.

With an LLM connected, the single agent writes the whole itinerary in one prompt from all tool
results. Without an LLM, it uses the same scheduling heuristics as the supervisor.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from .agents import parsing
from .agents.supervisor import _from_form, resolve_trip
from .config import settings
from .agents.budget import choose_hotel, choose_transport, tally
from .agents.research import score_place
from .agents.scheduler import build_itinerary
from .agents.validator import check_itinerary
from .agents.weather_agent import indoor_alternatives
from .llm import LLMError, get_llm
from .memory.store import get_store
from .tools import booking as booking_tool
from .tools import places as places_tool
from .tools import weather as weather_tool
from .tools.failures import FailureInjector, ToolError, call_with_retry

log = logging.getLogger(__name__)

SYSTEM = (
    "You are a travel-planning agent. Using ONLY the data provided, write a day-by-day itinerary that fits the "
    "budget. Return JSON: {\"days\": [{\"day\": 1, \"items\": [{\"time\": \"09:00\", \"end\": \"10:30\", "
    "\"type\": \"activity|meal|transport|lodging|transit\", \"meal\": \"lunch|dinner|breakfast|null\", "
    "\"name\": \"...\", \"place_id\": \"...\", \"cost\": 0}]}]}. Costs are totals in KRW for all travellers. "
    "Include breakfast, lunch, dinner, local transit, lodging each night and intercity transport if any."
)


def run_baseline(request: str = "", user_id: str = "default", inject_failures: dict | None = None,
                 form: dict | None = None) -> dict:
    t0 = time.time()
    inj = FailureInjector(inject_failures)
    parsed = _from_form(form) if form else parsing.rule_based_parse(request)
    trip, _ = resolve_trip(parsed)
    if trip is None:
        return {"status": "failed", "error": "missing destination", "runtime": time.time() - t0}
    days, travelers, budget = trip["days"], trip["travelers"], trip["budget_krw"]
    start = dt.date.fromisoformat(trip["start_date"])
    mem = get_store().get_preferences(user_id)
    likes = (set(mem["likes"]) - set(parsed["dislikes"])) | set(parsed["likes"])
    dislikes = (set(mem["dislikes"]) - set(parsed["likes"])) | set(parsed["dislikes"])
    key = trip.get("catalog_key")
    try:
        # one sequential chain of tool calls - any persistent failure interrupts the whole plan
        def places():
            if key and settings.places_mode != "live":
                return places_tool.search_catalog(key)
            if settings.places_mode == "mock":
                raise ToolError("not in the offline catalog")
            return places_tool.search_osm(trip["destination"], trip["lat"], trip["lon"], trip["price_index"])
        found, _ = call_with_retry("places", places, inj)
        (forecast, _src), _ = call_with_retry(
            "weather", lambda: weather_tool.get_forecast(trip["destination"], trip["lat"], trip["lon"], start, days), inj)
        lodging, _ = call_with_retry("booking", lambda: booking_tool.search_lodging(trip, start, max(0, days - 1),
                                                                                       travelers), inj)
        transport, _ = call_with_retry("transport", lambda: booking_tool.search_transport(trip), inj)
    except ToolError as exc:
        return {"status": "failed", "error": str(exc), "runtime": time.time() - t0, "trip": trip}

    candidates = []
    for p in found:
        s = score_place(p, likes, dislikes)
        if s is not None:
            candidates.append(dict(p, score=s))
    candidates.sort(key=lambda c: -c["score"])
    tr = choose_transport(transport, budget, {})
    hotel = choose_hotel(lodging, (budget - (tr["total"] if tr else 0)) * 0.4, {})
    style = "foodie" if "food" in likes else "normal"
    prices = {"tickets": {c["id"]: booking_tool.quote_ticket(c, start, travelers) for c in candidates
                          if c["kind"] == "attraction"},
              "breakfast_per_day": booking_tool.breakfast(trip, style) * travelers,
              "transit_per_day": booking_tool.local_transit(trip, days, travelers) // days,
              "meal_style": style}
    state = {"trip": trip, "candidates": candidates, "preferences": {"likes": sorted(likes), "dislikes": sorted(dislikes)},
             "weather": {"forecast": forecast, "alternatives": indoor_alternatives(candidates)},
             "prices": prices, "selection": {"hotel": hotel, "transport": tr}, "constraints": {},
             "lodging_options": lodging, "transport_options": transport}

    itinerary = None
    llm = get_llm()
    if llm is not None:
        itinerary = _llm_itinerary(llm, state)
    if itinerary is None:
        itinerary = build_itinerary(state)
    itinerary["totals"] = tally(itinerary)
    state["itinerary"] = itinerary
    validation = check_itinerary(state)
    return {"status": "done", "state": state, "validation": validation, "runtime": time.time() - t0, "trip": trip}


def _llm_itinerary(llm, state: dict) -> dict | None:
    trip = state["trip"]
    data = {
        "trip": trip, "preferences": state["preferences"],
        "forecast": [{"day": i + 1, "weather": d["description"], "rain": d["bad"]}
                     for i, d in enumerate(state["weather"]["forecast"])],
        "places": [{"id": c["id"], "name": c["name"], "kind": c["kind"], "indoor": c.get("indoor"),
                    "tags": c["tags"], "cost_total": c.get("cost", 0) * trip["travelers"], "meal": c.get("meal")}
                   for c in state["candidates"][:35]],
        "hotels": [{"name": h["name"], "total": h["total"], "available": h["available"]}
                   for h in state["lodging_options"]],
        "transport": [{"name": t["name"], "total_return": t["total"], "hours": t["hours"]}
                      for t in state["transport_options"]],
        "breakfast_per_day": state["prices"]["breakfast_per_day"],
        "local_transit_per_day": state["prices"]["transit_per_day"],
    }
    try:
        out = llm.chat_json(SYSTEM, str(data))
        days = out.get("days") if isinstance(out, dict) else None
        if not days:
            return None
        clean = []
        for i, d in enumerate(days[: trip["days"]]):
            items = [{"time": str(x.get("time", "—")), "end": str(x.get("end", "—")), "type": x.get("type", "activity"),
                      "meal": x.get("meal"), "name": str(x.get("name", "")), "place_id": x.get("place_id"),
                      "cost": int(float(x.get("cost") or 0))} for x in d.get("items", [])]
            clean.append({"day": i + 1, "date": (dt.date.fromisoformat(trip["start_date"]) + dt.timedelta(days=i)).isoformat(),
                          "weather": {"description": state["weather"]["forecast"][i]["description"],
                                      "bad": state["weather"]["forecast"][i]["bad"]}, "items": items})
        return {"destination": trip["destination"], "days": clean, "hotel": state["selection"]["hotel"],
                "transport": state["selection"]["transport"], "meal_style": state["prices"]["meal_style"],
                "travelers": trip["travelers"], "currency": trip["currency"]}
    except (LLMError, ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        log.warning("baseline LLM itinerary failed: %s", exc)
        return None
