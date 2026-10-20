"""Weather agent: gets the forecast and suggests indoor alternatives for outdoor plans on bad days."""
from __future__ import annotations

import datetime as dt

from .. import bus
from ..state import PlanState, message
from ..tools import weather as weather_tool
from ..tools.failures import FailureInjector, ToolError, call_with_retry

PER_DAY = {"relaxed": 2, "balanced": 3, "packed": 4}


def indoor_alternatives(candidates: list[dict], k: int = 3) -> dict:
    """For every outdoor attraction, the best indoor attractions sharing a tag (or simply the best indoor ones)."""
    indoor = [c for c in candidates if c["kind"] == "attraction" and c.get("indoor")
              and "evening" not in c.get("tags", [])]
    alts = {}
    for c in candidates:
        if c["kind"] != "attraction" or c.get("indoor"):
            continue
        tags = set(c.get("tags", []))
        ranked = sorted(indoor, key=lambda x: (-len(tags & set(x.get("tags", []))), -x.get("score", 0)))
        alts[c["id"]] = [x["id"] for x in ranked[:k]]
    return alts


def weather_agent(state: PlanState) -> dict:
    trip = state["trip"]
    inj = FailureInjector(state.get("inject_failures"))
    start = dt.date.fromisoformat(trip["start_date"])
    lat, lon = trip.get("lat"), trip.get("lon")
    try:
        (forecast, source), errors = call_with_retry(
            "weather", lambda: weather_tool.get_forecast(trip["destination"], lat, lon, start, trip["days"]), inj)
        status = {"status": "recovered" if errors else "ok", "errors": errors, "source": source}
    except ToolError as exc:
        # graceful degradation: fall back to the seasonal estimate and flag it
        forecast = weather_tool.mock_forecast(trip["destination"], start, trip["days"], lat)
        status = {"status": "degraded", "errors": [str(exc)], "source": "offline seasonal estimate (API down)"}

    candidates = state.get("candidates", [])
    alternatives = indoor_alternatives(candidates)
    bad_days = [i + 1 for i, d in enumerate(forecast) if d["bad"]]
    weather = {"forecast": forecast, "source": status["source"], "bad_days": bad_days, "alternatives": alternatives}
    summary = ", ".join(f"day {i + 1}: {d['description']}" for i, d in enumerate(forecast))
    text = (f"Forecast ({status['source']}): {len(bad_days)} wet day(s)"
            + (f" - day {', '.join(map(str, bad_days))}" if bad_days else "")
            + f". Prepared indoor alternatives for {len(alternatives)} outdoor activities.")
    msgs = [message("weather", "supervisor", "forecast_and_adjust",
                    {"destination": trip["destination"], "start": trip["start_date"], "days": trip["days"]},
                    {"forecast": summary, "bad_days": bad_days, "outdoor_with_indoor_alternatives": len(alternatives)},
                    "degraded" if status["status"] == "degraded" else "ok", "; ".join(status["errors"]) or None,
                    text=text)]

    # Do we have enough indoor activities for the wet days? If not, ask the Research agent directly.
    mailbox = []
    indoor = sum(1 for c in candidates if c["kind"] == "attraction" and c.get("indoor"))
    needed = len(bad_days) * PER_DAY.get(trip.get("pace", "balanced"), 3)
    if bad_days and candidates and indoor < needed and not bus.already_asked(state, "weather", "research", "need_indoor_activities"):
        mailbox.append(bus.request(
            "weather", "research", "need_indoor_activities",
            {"rainy_days": bad_days, "needed": needed, "available": indoor, "missing": needed - indoor},
            f"Rain is expected on day {', '.join(map(str, bad_days))} but there are only {indoor} indoor "
            f"activities for {needed} slots. Can you find {needed - indoor} more indoor options?"))
    return {"weather": weather, "tool_status": {"weather": status}, "inject_failures": inj.remaining(),
            "messages": msgs + mailbox, "mailbox": mailbox}


@bus.on_reply("weather", "need_indoor_activities")
def on_indoor_reply(state: dict, reply: dict) -> dict:
    """Research added indoor options: rebuild the swap table and tell Budget to price the new places."""
    weather = dict(state.get("weather") or {})
    weather["alternatives"] = indoor_alternatives(state.get("candidates", []))
    added = (reply.get("result") or {}).get("added", [])
    follow = bus.notice("weather", "budget", "new_candidates", {"names": added},
                        f"Research added {len(added)} indoor places; please price them so the rainy-day plan "
                        f"stays within budget.") if added else None
    return {"updates": {"weather": weather}, "followups": [follow] if follow else []}
