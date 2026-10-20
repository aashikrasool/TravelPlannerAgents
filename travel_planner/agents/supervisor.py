"""Supervisor agent: interprets the goal, delegates tasks, merges results and writes the final answer."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from .. import world
from ..data import catalog
from ..llm import LLMError, get_llm
from ..state import PlanState, message
from . import parsing
from .budget import tally
from .scheduler import build_itinerary

log = logging.getLogger(__name__)

VOCAB = sorted(parsing.PREF_KEYWORDS)
MAX_DAYS = 14

PARSE_SYSTEM = (
    "You are the Supervisor of a multi-agent travel planner. Extract the trip request into JSON with keys: "
    "destination (city name), destination_country (country name or null), origin (city or null), days (int), "
    "budget (number), currency (ISO code like USD, EUR, KRW), travelers (int), start_date (YYYY-MM-DD or null), "
    f"likes (list), dislikes (list). likes/dislikes may only contain these tags: {VOCAB}. "
    "Use null when the request does not say."
)
NOTES_SYSTEM = (
    "You are the Supervisor of a multi-agent travel planner. From the traveller's extra notes, extract JSON "
    f'{{"likes": [...], "dislikes": [...]}} using only these tags: {VOCAB}.'
)
SUMMARY_SYSTEM = (
    "You are the Supervisor of a multi-agent travel planner. Write a short, friendly summary (4-6 sentences) of "
    "the final itinerary for the traveller: highlight how it matches their preferences, weather adjustments, the "
    "total cost versus budget, and any trade-offs the user chose. Do not invent places or prices."
)


# ---------------------------------------------------------------- understanding the request

def _merge_llm(parsed: dict, request: str) -> tuple[dict, bool]:
    llm = get_llm()
    if llm is None or not request:
        return parsed, False
    try:
        data = llm.chat_json(PARSE_SYSTEM, request)
        if not isinstance(data, dict):
            return parsed, False
    except LLMError as exc:
        log.warning("Supervisor LLM parse failed, using rule-based parse: %s", exc)
        return parsed, False
    if not parsed.get("destination_place") and data.get("destination"):
        ctry = world.country_by_name(str(data.get("destination_country") or ""))
        place = world.find_city(str(data["destination"]), ctry["iso"] if ctry else None)
        if place:
            parsed["destination_place"], parsed["destination"] = place, place["name"]
    if not parsed.get("origin_place") and data.get("origin"):
        place = world.find_city(str(data["origin"]))
        if place:
            parsed["origin_place"], parsed["origin"] = place, place["name"]
    if not parsed.get("currency") and data.get("currency") and str(data["currency"]).upper() in world.USD_RATES:
        parsed["currency"] = str(data["currency"]).upper()
    for key in ("days", "travelers"):
        if not parsed.get(key) and data.get(key):
            try:
                parsed[key] = int(data[key])
            except (TypeError, ValueError):
                pass
    if not parsed.get("budget") and data.get("budget"):
        try:
            parsed["budget"] = float(str(data["budget"]).replace(",", ""))
        except ValueError:
            pass
    if not parsed.get("start_date") and data.get("start_date"):
        try:
            parsed["start_date"] = dt.date.fromisoformat(str(data["start_date"]))
        except ValueError:
            pass
    for key in ("likes", "dislikes"):
        extra = [t for t in (data.get(key) or []) if t in VOCAB]
        parsed[key] = sorted(set(parsed.get(key, [])) | set(extra))
    parsed["likes"] = [t for t in parsed["likes"] if t not in parsed["dislikes"]]
    return parsed, True


def _from_form(form: dict) -> dict:
    """Structured input from the dropdown form (web app / interactive CLI)."""
    dest = world.find_city(form["city"], form.get("country")) if form.get("city") else None
    if dest is None and form.get("country"):
        cap = world.country(form["country"]).get("capital")
        dest = world.find_city(cap, form["country"]) if cap else None
    origin = world.find_city(form["origin_city"], form.get("origin_country")) if form.get("origin_city") else None
    notes = form.get("notes") or ""
    note_prefs = parsing.extract_preferences(notes) if notes else {"likes": [], "dislikes": []}
    llm = get_llm()
    if llm is not None and notes:
        try:
            data = llm.chat_json(NOTES_SYSTEM, notes)
            for k in ("likes", "dislikes"):
                note_prefs[k] = sorted(set(note_prefs[k]) | {t for t in data.get(k, []) if t in VOCAB})
        except (LLMError, AttributeError):
            pass
    start = form.get("start_date")
    return {
        "destination": dest["name"] if dest else None, "destination_place": dest,
        "origin": origin["name"] if origin else None, "origin_place": origin,
        "days": form.get("days"), "travelers": form.get("travelers"),
        "budget": form.get("budget"), "currency": form.get("currency"),
        "start_date": dt.date.fromisoformat(start) if isinstance(start, str) and start else start,
        "likes": sorted((set(form.get("interests") or []) | set(note_prefs["likes"])) - set(form.get("avoid") or [])),
        "dislikes": sorted(set(form.get("avoid") or []) | set(note_prefs["dislikes"])),
        "style": form.get("style") or "standard", "pace": form.get("pace") or "balanced",
    }


def resolve_trip(parsed: dict) -> tuple[Optional[dict], list[str]]:
    """Turn parsed input into a fully-specified trip (location, prices, currency, dates)."""
    assumptions = []
    place = parsed.get("destination_place")
    if place is None and parsed.get("destination"):
        place = world.find_city(parsed["destination"])
    if place is None:
        return None, assumptions
    if place.get("inferred_from_country"):
        assumptions.append(f"only a country was named - planning for its capital, {place['name']}")
    ctry = world.country(place["country"])
    origin = parsed.get("origin_place") or (world.find_city(parsed["origin"]) if parsed.get("origin") else None)

    days = parsed.get("days")
    if not days:
        days = 3
        assumptions.append("trip length not given - assumed 3 days")
    days = max(1, min(MAX_DAYS, int(days)))
    travelers = parsed.get("travelers")
    if not travelers:
        travelers = 1
        assumptions.append("number of travellers not given - assumed 1")
    currency = (parsed.get("currency") or (world.country(origin["country"]).get("currency") if origin else None)
                or ctry.get("currency") or "USD").upper()
    if world.usd_rate(currency) is None:
        assumptions.append(f"no exchange rate for {currency} - using USD")
        currency = "USD"
    p_index = world.price_index(place["country"])
    budget = parsed.get("budget")
    if not budget:
        per_day = 110000 * p_index
        budget = world.from_base(per_day * days * int(travelers), currency)
        assumptions.append(f"no budget given - assumed about {world.money(per_day, currency)} per person per day")
    start = parsed.get("start_date") or (dt.date.today() + dt.timedelta(days=7))
    if not parsed.get("start_date"):
        assumptions.append(f"no start date given - assumed {start.isoformat()}")
    catalog_key = catalog.find_city(place["name"]) if place["country"] == "KR" else None

    trip = {
        "destination": place["name"], "country": place["country"], "country_name": ctry.get("name", ""),
        "region": place.get("region"), "lat": place["lat"], "lon": place["lon"],
        "population": place.get("population"), "price_index": p_index, "catalog_key": catalog_key,
        "origin": origin["name"] if origin else None, "origin_country": origin["country"] if origin else None,
        "origin_lat": origin["lat"] if origin else None, "origin_lon": origin["lon"] if origin else None,
        "origin_price_index": world.price_index(origin["country"]) if origin else None,
        "days": days, "travelers": int(travelers), "currency": currency, "budget": float(budget),
        "budget_krw": world.to_base(float(budget), currency), "start_date": start.isoformat(),
        "style": parsed.get("style") or "standard", "pace": parsed.get("pace") or "balanced",
        "assumptions": assumptions,
    }
    return trip, assumptions


def supervisor_plan(state: PlanState) -> dict:
    request = state.get("request") or ""
    if state.get("form"):
        parsed, used_llm = _from_form(state["form"]), get_llm() is not None and bool(state["form"].get("notes"))
        source = "form"
    else:
        parsed, used_llm = _merge_llm(parsing.rule_based_parse(request), request)
        source = "text"
    trip, _ = resolve_trip(parsed)
    if trip is None:
        return {"status": "failed",
                "summary": "I could not find a destination. Pick a country and city, or write e.g. "
                           "'Plan 3 days in Lisbon with 600 EUR'.",
                "messages": [message("supervisor", "user", "parse_request", request or state.get("form"), None,
                                     "failed", "missing destination", text="No destination found in the request.")]}
    trip["input_source"], trip["parsed_with_llm"] = source, used_llm

    memory_prefs = state.get("preferences", {}) or {}
    req_likes, req_dislikes = set(parsed.get("likes", [])), set(parsed.get("dislikes", []))
    likes = (set(memory_prefs.get("likes", [])) - req_dislikes) | req_likes
    dislikes = (set(memory_prefs.get("dislikes", [])) - req_likes) | req_dislikes
    trip["request_likes"], trip["request_dislikes"] = sorted(req_likes), sorted(req_dislikes)
    prefs = {"likes": sorted(likes), "dislikes": sorted(dislikes),
             "from_memory": sorted((set(memory_prefs.get("likes", [])) | set(memory_prefs.get("dislikes", [])))
                                   - req_likes - req_dislikes)}
    where = f"{trip['destination']}, {trip['country_name']}"
    tasks = [
        {"agent": "research", "task": "find_candidates", "input": {"destination": where, **prefs},
         "text": f"Find activities and restaurants in {where} for someone who likes "
                 f"{', '.join(prefs['likes']) or 'a bit of everything'}"
                 + (f" and avoids {', '.join(prefs['dislikes'])}." if prefs["dislikes"] else ".")},
        {"agent": "booking", "task": "gather_lodging_and_transport",
         "input": {"destination": where, "origin": trip["origin"], "nights": trip["days"] - 1},
         "text": f"Find places to stay for {trip['days'] - 1} night(s)"
                 + (f" and transport from {trip['origin']}." if trip["origin"] else ".")},
        {"agent": "weather", "task": "forecast_and_adjust", "depends_on": "research",
         "input": {"start": trip["start_date"], "days": trip["days"]},
         "text": f"Check the weather from {trip['start_date']} and prepare indoor alternatives."},
        {"agent": "budget", "task": "price_and_allocate", "depends_on": ["weather", "booking"],
         "input": {"budget": world.money(trip["budget_krw"], trip["currency"])},
         "text": f"Price everything and keep the plan under {world.money(trip['budget_krw'], trip['currency'])}."},
    ]
    msgs = [message("supervisor", "supervisor", "understand_request", request or state.get("form"),
                    {k: trip[k] for k in ("destination", "country_name", "origin", "days", "travelers", "currency",
                                          "start_date", "style", "pace")},
                    text=f"Planning {trip['days']} day(s) in {where} for {trip['travelers']} traveller(s), budget "
                         f"{world.money(trip['budget_krw'], trip['currency'])} (input from {source}).")]
    msgs += [message("supervisor", t["agent"], t["task"], t["input"], None, "sent", kind="task", text=t["text"])
             for t in tasks]
    return {"trip": trip, "preferences": prefs, "tasks": tasks, "constraints": state.get("constraints") or {},
            "iterations": 0, "hops": 0, "status": "planning", "messages": msgs}


# ---------------------------------------------------------------- integrating + answering

def supervisor_merge(state: PlanState) -> dict:
    itinerary = build_itinerary(state)
    totals = tally(itinerary)
    itinerary["totals"] = totals
    n_act = sum(1 for d in itinerary["days"] for i in d["items"] if i["type"] == "activity")
    swaps = [i["notes"] for d in itinerary["days"] for i in d["items"] if "Indoor alternative" in str(i.get("notes"))]
    cur = state["trip"]["currency"]
    return {"itinerary": itinerary,
            "messages": [message("supervisor", "validator", "merge_draft",
                                 {"iteration": state.get("iterations", 0)},
                                 {"activities": n_act, "weather_swaps": len(swaps), "draft_total": totals["total"]},
                                 kind="task",
                                 text=f"Merged the specialists' results into a draft with {n_act} activities "
                                      f"({len(swaps)} weather swaps), total {world.money(totals['total'], cur)}. "
                                      f"Please validate.")]}


def _template_summary(state: PlanState) -> str:
    trip, it, val = state["trip"], state["itinerary"], state.get("validation", {})
    prefs = state.get("preferences", {})
    cur = trip["currency"]
    total = val.get("total", it["totals"]["total"])
    parts = [f"Here is your {trip['days']}-day plan for {trip['destination']}, {trip['country_name']}"
             + (f" for {trip['travelers']} travellers" if trip["travelers"] > 1 else "") + "."]
    if prefs.get("likes"):
        parts.append(f"It leans into what you enjoy ({', '.join(prefs['likes'])})"
                     + (f" and avoids {', '.join(prefs['dislikes'])}." if prefs.get("dislikes") else "."))
    rainy = [d["day"] for d in it["days"] if d["weather"]["bad"]]
    if rainy:
        days_txt = f"day {rainy[0]}" if len(rainy) == 1 else "days " + ", ".join(map(str, rainy))
        parts.append(f"Bad weather is expected on {days_txt}, so outdoor plans there were swapped for indoor ones.")
    parts.append(f"Estimated total: {world.money(total, cur)} against a budget of "
                 f"{world.money(trip['budget_krw'], cur)}"
                 + (f" ({world.money(trip['budget_krw'] - total, cur)} left)." if total <= trip["budget_krw"] else "."))
    if state.get("decisions"):
        parts.append("Changes you approved: " + "; ".join(d["label"] for d in state["decisions"]) + ".")
    return " ".join(parts)


def supervisor_finalize(state: PlanState) -> dict:
    summary = _template_summary(state)
    llm = get_llm()
    if llm is not None:
        it = state["itinerary"]
        brief = {"trip": {k: state["trip"][k] for k in ("destination", "country_name", "days", "travelers")},
                 "budget": world.money(state["trip"]["budget_krw"], state["trip"]["currency"]),
                 "total": world.money(state.get("validation", {}).get("total", 0), state["trip"]["currency"]),
                 "preferences": state.get("preferences"), "decisions": [d["label"] for d in state.get("decisions", [])],
                 "days": [{"day": d["day"], "weather": d["weather"]["description"],
                           "plan": [i["name"] for i in d["items"] if i["type"] in ("activity", "meal")]}
                          for d in it["days"]]}
        try:
            summary = llm.chat(SUMMARY_SYSTEM, str(brief)).strip() or summary
        except LLMError as exc:
            log.warning("summary LLM failed: %s", exc)
    passed = state.get("validation", {}).get("passed", False)
    return {"summary": summary, "status": "done" if passed else "failed",
            "messages": [message("supervisor", "user", "final_itinerary", None,
                                 {"passed_validation": passed, "total": state.get("validation", {}).get("total")},
                                 text="Final itinerary delivered to the traveller." if passed else
                                 "Delivered the best plan found; some issues remain.")]}
