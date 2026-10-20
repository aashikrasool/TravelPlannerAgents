"""Research agent: finds places, activities and restaurants that match the traveller - anywhere."""
from __future__ import annotations

import logging

from .. import bus
from ..config import settings
from ..llm import LLMError, get_llm
from ..state import PlanState, message
from ..tools import places as places_tool
from ..tools.failures import FailureInjector, ToolError, call_with_retry

log = logging.getLogger(__name__)

SYSTEM = (
    "You are the Research agent in a multi-agent travel planner. Rate how well each candidate place fits the "
    "traveller's preferences on a 0-10 scale and give a one-line reason (max 15 words). Only use ids from the list. "
    'Return JSON: {"ratings": [{"id": "...", "fit": 0-10, "reason": "..."}]}'
)


def score_place(place: dict, likes: set, dislikes: set) -> float | None:
    """Rule-based preference score. None means the place is excluded (matches a dislike)."""
    tags = set(place.get("tags", []))
    if place["kind"] == "attraction" and tags & dislikes:
        return None
    if place["kind"] == "restaurant" and "seafood" in dislikes and "seafood" in tags:
        return None
    s = float(place.get("popularity", 3))
    s += 3.0 * len(tags & (likes - {"food"}))
    if "food" in likes and ("food" in tags):
        s += 2.0 if place["kind"] == "attraction" else 0.5 * place.get("popularity", 3)
    if not place.get("cost"):
        s += 0.5
    return s


def fetch_places(trip: dict, constraints: dict, inj: FailureInjector) -> tuple[list[dict], dict]:
    key = trip.get("catalog_key")
    mode = settings.places_mode.lower()

    def fetch():
        if key and mode != "live":
            return places_tool.search_catalog(key)
        if mode == "mock":
            raise ToolError(f"{trip['destination']} is not in the offline catalog and live search is off "
                            f"(PLACES_MODE=mock)")
        return places_tool.search_osm(trip["destination"], trip["lat"], trip["lon"], trip.get("price_index", 1.0))

    try:
        found, errors = call_with_retry("places", fetch, inj)
        source = "curated catalog" if key and mode != "live" else "OpenStreetMap (live)"
        return found, {"status": "recovered" if errors else "ok", "errors": errors, "source": source}
    except ToolError as exc:
        if constraints.get("allow_generic"):
            return (places_tool.generic_places(trip["destination"], trip.get("lat"), trip.get("lon"),
                                               trip.get("price_index", 1.0)),
                    {"status": "degraded", "errors": [str(exc)], "source": "generic placeholders (accepted by user)"})
        return [], {"status": "failed", "errors": [str(exc)], "source": None}


def _llm_refine(scored: list[dict], prefs: dict, trip: dict) -> int:
    llm = get_llm()
    if llm is None:
        return 0
    top = [c for c in scored if c["kind"] == "attraction"][:25]
    if not top:
        return 0
    listing = "\n".join(f"- {c['id']}: {c['name']} [{', '.join(c['tags'])}] {c.get('description', '')}" for c in top)
    user = (f"Destination: {trip['destination']}, {trip.get('country_name', '')}. "
            f"Traveller likes: {prefs.get('likes') or 'not stated'}; dislikes: {prefs.get('dislikes') or 'none'}.\n"
            f"Candidates:\n{listing}")
    try:
        data = llm.chat_json(SYSTEM, user)
        ratings = data.get("ratings", []) if isinstance(data, dict) else data
        by_id = {c["id"]: c for c in top}
        n = 0
        for r in ratings:
            c = by_id.get(str(r.get("id")))
            if c is None:
                continue                    # ignore hallucinated ids
            fit = max(0.0, min(10.0, float(r.get("fit", 5))))
            c["score"] = round(c["score"] + (fit - 5) * 0.6, 2)
            c["reason"] = str(r.get("reason", ""))[:120]
            n += 1
        return n
    except (LLMError, ValueError, TypeError, AttributeError) as exc:
        log.warning("Research LLM refinement skipped: %s", exc)
        return 0


def score_all(found: list[dict], prefs: dict) -> tuple[list[dict], list[str]]:
    likes, dislikes = set(prefs.get("likes", [])), set(prefs.get("dislikes", []))
    scored, excluded = [], []
    for p in found:
        s = score_place(p, likes, dislikes)
        if s is None:
            excluded.append(p["name"])
            continue
        p = dict(p, score=round(s, 2))
        matched = sorted(set(p.get("tags", [])) & likes)
        if matched and p["kind"] == "attraction":
            p.setdefault("reason", f"Matches your interest in {', '.join(matched)}")
        scored.append(p)
    scored.sort(key=lambda c: -c["score"])
    return scored, excluded


def activity_centre(candidates: list[dict], n: int = 8) -> dict | None:
    top = [c for c in candidates if c["kind"] == "attraction" and c.get("lat") is not None][:n]
    if not top:
        return None
    areas: dict[str, int] = {}
    for c in top:
        areas[c.get("area") or "centre"] = areas.get(c.get("area") or "centre", 0) + 1
    area = max(areas, key=areas.get)
    core = [c for c in top if (c.get("area") or "centre") == area] if areas[area] > 1 else top
    return {"lat": round(sum(c["lat"] for c in core) / len(core), 4),
            "lon": round(sum(c["lon"] for c in core) / len(core), 4),
            "area": area if areas[area] > 1 else "the city centre", "n": len(core)}


def research_agent(state: PlanState) -> dict:
    trip = state["trip"]
    prefs = state.get("preferences", {})
    constraints = state.get("constraints", {}) or {}
    inj = FailureInjector(state.get("inject_failures"))
    found, status = fetch_places(trip, constraints, inj)
    scored, excluded = score_all(found, prefs)
    refined = _llm_refine(scored, prefs, trip)
    scored.sort(key=lambda c: -c["score"])

    n_att = sum(1 for c in scored if c["kind"] == "attraction")
    result = {"attractions": n_att, "restaurants": len(scored) - n_att, "excluded_by_preference": excluded[:8],
              "llm_rated": refined, "source": status["source"]}
    msg_status = {"ok": "ok", "recovered": "ok", "degraded": "degraded", "failed": "failed"}[status["status"]]
    out_msgs = [message("research", "supervisor", "find_candidates",
                        {"destination": trip["destination"], **{k: prefs.get(k) for k in ("likes", "dislikes")}},
                        result, msg_status, "; ".join(status["errors"]) or None,
                        text=f"Found {n_att} activities and {len(scored) - n_att} restaurants in {trip['destination']}"
                             f" ({status['source'] or 'no source'}); excluded {len(excluded)} that match dislikes.")]
    mailbox = []
    centre = activity_centre(scored)
    if centre and trip["days"] > 1 and not bus.already_asked(state, "research", "booking", "prefer_area"):
        mailbox.append(bus.request(
            "research", "booking", "prefer_area", centre,
            f"Most of the best-matching activities are around {centre['area']}. Please rank hotels by "
            f"distance to that area so travel time stays short."))
    return {"candidates": scored, "tool_status": {"places": status}, "inject_failures": inj.remaining(),
            "messages": out_msgs + mailbox, "mailbox": mailbox}


# ---------------------------------------------------------------- peer requests handled by Research

@bus.handles("research", "need_indoor_activities")
def handle_need_indoor(state: dict, msg: dict) -> dict:
    """Weather asks for more indoor options for rainy days."""
    trip = state["trip"]
    have = {c["id"] for c in state.get("candidates", [])}
    extra = places_tool.extra_indoor_places(trip["destination"], trip.get("lat"), trip.get("lon"),
                                            trip.get("price_index", 1.0), exclude=have)
    needed = msg["input"].get("missing", 3)
    extra_scored, _ = score_all(extra, state.get("preferences", {}))
    for c in extra_scored:
        c["score"] = round(c["score"] - 1.0, 2)          # placeholders rank below real places
        c["reason"] = "Indoor option added for rainy days"
    added = extra_scored[: max(needed, 2)]
    candidates = sorted(state.get("candidates", []) + added, key=lambda c: -c["score"])
    if not added:
        return {"result": {"added": []},
                "text": "No further indoor places are available here - on wet days keep the outdoor plans short "
                        "and bring rain gear."}
    return {"updates": {"candidates": candidates},
            "result": {"added": [c["name"] for c in added]},
            "text": f"Added {len(added)} indoor options for the rainy days: " + ", ".join(c['name'] for c in added[:3])
                    + ("..." if len(added) > 3 else "")}


@bus.handles("research", "flag_expensive")
def handle_flag_expensive(state: dict, msg: dict) -> dict:
    """Budget tells Research the per-person activity budget; Research down-ranks pricier sights."""
    cap = msg["input"]["cap_per_person"]
    flagged, candidates = [], []
    for c in state.get("candidates", []):
        c = dict(c)
        if c["kind"] == "attraction" and (c.get("cost") or 0) > cap:
            c["score"] = round(c["score"] - 2.5, 2)
            c["budget_note"] = "pricey for this budget"
            flagged.append(c["name"])
        candidates.append(c)
    candidates.sort(key=lambda c: -c["score"])
    free = sum(1 for c in candidates if c["kind"] == "attraction" and not c.get("cost"))
    return {"updates": {"candidates": candidates},
            "result": {"flagged": flagged, "free_options": free},
            "text": f"Down-ranked {len(flagged)} activities above the per-person cap; {free} free activities "
                    f"remain at the top of the list."}
