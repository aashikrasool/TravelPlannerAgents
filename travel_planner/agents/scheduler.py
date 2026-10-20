"""Builds the day-by-day itinerary from the specialists' results (used by the supervisor's merge step)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from ..tools.geo import order_by_proximity, travel_minutes

DAY_START = 9 * 60
LUNCH_EARLIEST, LUNCH_LATEST = 11 * 60 + 30, 13 * 60 + 30
DINNER_AT = 18 * 60
DEPARTURE_AT = 17 * 60 + 30
MEAL_CAP = {"budget": 12000, "normal": 25000, "foodie": 10**9}


def hhmm(minutes: int) -> str:
    minutes = int(minutes)
    plus = f" (+{minutes // 1440}d)" if minutes >= 1440 else ""
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}{plus}"


def _primary(c: dict) -> str:
    return (c.get("tags") or ["other"])[0]


def pick_activities(pool: list[dict], used: set, n_hours: float, bad: bool, alternatives: dict,
                    by_id: dict, max_items: int = 3, cost_scale: float = 1.0,
                    origin: Optional[dict] = None) -> list[dict]:
    """Greedy choice of the day's activities: best score, varied tags, one area, weather-aware."""
    full_pool = pool
    picks: list[dict] = []
    hours = 0.0
    while len(picks) < max_items:
        best, best_val = None, -1e9
        for c in pool:
            if c["id"] in used or "evening" in c.get("tags", []):
                continue
            val = c.get("score", 0)
            val -= 1.5 * sum(1 for p in picks if _primary(p) == _primary(c))
            val -= cost_scale * (c.get("cost") or 0) / 20000
            if picks:
                val -= travel_minutes(picks[-1], c) / 8       # keep the day in one area
            elif origin is not None:
                val -= travel_minutes(origin, c) / 25         # start reasonably close to the hotel
            if val > best_val:
                best, best_val = c, val
        if best is None:
            break
        chosen = dict(best)
        if bad and not best.get("indoor"):
            # weather agent's suggestion: swap the outdoor activity for an indoor alternative
            alt = next((by_id[a] for a in alternatives.get(best["id"], [])
                        if a in by_id and a not in used and a not in {p["id"] for p in picks}), None)
            if alt is None:
                # no indoor alternative: skip it today, it stays available for a dry day
                pool = [c for c in pool if c["id"] != best["id"]]
                continue
            chosen = dict(alt)
            chosen["swap_note"] = f"Indoor alternative to {best['name']} (rain forecast)"
            pool = [c for c in pool if c["id"] != best["id"]]   # don't swap the same outdoor plan twice
        leg = travel_minutes(picks[-1] if picks else origin, chosen) / 60
        if hours + leg + chosen.get("hours", 1.5) > n_hours and picks:
            break
        picks.append(chosen)
        used.add(chosen["id"])
        hours += chosen.get("hours", 1.5) + leg
    if not picks:
        # every indoor option is used up: keep the day useful with the best remaining activity
        rest = [c for c in full_pool if c["id"] not in used and "evening" not in c.get("tags", [])]
        if rest:
            best = dict(max(rest, key=lambda c: c.get("score", 0)))
            if bad and not best.get("indoor"):
                best["swap_note"] = "Rain expected - bring rain gear, or move this to a drier day"
            used.add(best["id"])
            picks.append(best)
    return picks


def _route_minutes(picks: list[dict], start: Optional[dict]) -> int:
    total, cur = 0, start
    for p in picks:
        total += travel_minutes(cur, p) if cur else 15
        cur = p
    return total + (travel_minutes(cur, start) if start else 15)


def pick_restaurant(restaurants: list[dict], meal: str, near: Optional[dict], used: set, style: str,
                    price_index: float = 1.0) -> Optional[dict]:
    cap = MEAL_CAP.get(style, MEAL_CAP["normal"]) * price_index
    pool = [r for r in restaurants if r.get("meal") in (meal, "any")]
    if not pool:
        return None
    affordable = [r for r in pool if r.get("cost", 0) <= cap] or sorted(pool, key=lambda r: r.get("cost", 0))[:2]

    def value(r: dict) -> float:
        v = r.get("score", r.get("popularity", 3))
        if r["id"] in used:
            v -= 3.0                      # prefer variety, but a repeat beats a long detour
        if near is not None:
            v -= travel_minutes(near, r) / 8
        if style == "budget":
            v -= r.get("cost", 0) / (4000 * price_index)
        return v

    best = max(affordable, key=value)
    used.add(best["id"])
    return best


def build_itinerary(state: dict) -> dict:
    trip = state["trip"]
    prices = state.get("prices", {})
    selection = state.get("selection", {})
    constraints = state.get("constraints", {}) or {}
    weather = state.get("weather", {}) or {}
    forecast = weather.get("forecast", [])
    alternatives = weather.get("alternatives", {})
    excluded = set(constraints.get("excluded_ids", []))

    candidates = [c for c in state.get("candidates", []) if c["id"] not in excluded]
    attractions = sorted([c for c in candidates if c["kind"] == "attraction"], key=lambda c: -c.get("score", 0))
    restaurants = [c for c in candidates if c["kind"] == "restaurant"]
    by_id = {c["id"]: c for c in candidates}
    likes = set((state.get("preferences") or {}).get("likes", []))

    hotel = selection.get("hotel")
    transport = selection.get("transport")
    travelers = trip["travelers"]
    style = prices.get("meal_style", "normal")
    start = dt.date.fromisoformat(trip["start_date"])
    days = trip["days"]
    budget_per_day = max(1, trip["budget_krw"] / days)
    cost_scale = 1.0 if budget_per_day / travelers > 120000 * trip.get("price_index", 1.0) else 2.0
    if constraints.get("prefer_free"):
        cost_scale = 8.0
    used: set = set()
    used_rest: set = set()
    hotel_point = {"lat": hotel.get("lat"), "lon": hotel.get("lon")} if hotel and hotel.get("lat") else None

    out_days = []
    for i in range(days):
        date = start + dt.timedelta(days=i)
        fc = forecast[i] if i < len(forecast) else {}
        bad = bool(fc.get("bad"))
        first, last = i == 0, i == days - 1
        items: list[dict] = []
        cursor = DAY_START

        arrive = None
        if first and transport:
            # leave early enough to arrive around late morning; long-haul trips arrive later in the day
            depart = int(max(6 * 60, 11 * 60 - transport["hours"] * 60))
            arrive = depart + int(transport["hours"] * 60)
            items.append({"time": hhmm(depart), "end": hhmm(arrive), "type": "transport",
                          "name": f"{transport['name']} to {trip['destination']}",
                          "cost": transport["total"] // 2,
                          "notes": "Outbound journey" + (" - arrives late, so day 1 is for settling in"
                                                         if arrive >= 16 * 60 else "")})
            cursor = max(DAY_START, arrive + 45)
        else:
            items.append({"time": "08:30", "end": "09:00", "type": "meal", "meal": "breakfast",
                          "name": "Breakfast near the hotel" if not first else "Breakfast",
                          "cost": prices.get("breakfast_per_day", 0)})
        items.append({"time": "—", "end": "—", "type": "transit", "name": "Local transport (subway / bus / taxi)",
                      "cost": prices.get("transit_per_day", 0)})

        end_limit = (DEPARTURE_AT - 90) if (last and transport) else DINNER_AT
        lunch_needed = cursor <= LUNCH_LATEST
        hours_avail = (end_limit - cursor) / 60 - (1.5 if lunch_needed else 0.3)
        pace_max = {"relaxed": 2, "balanced": 3, "packed": 4}.get(trip.get("pace", "balanced"), 3)
        n_max = min(pace_max, 3 if hours_avail >= 5 else 2 if hours_avail >= 2.5 else 1) \
            if pace_max <= 3 else (4 if hours_avail >= 6.5 else 3 if hours_avail >= 5 else 2)
        picks = pick_activities(attractions, used, hours_avail, bad, alternatives, by_id, n_max, cost_scale,
                                hotel_point) if hours_avail >= 1.0 else []
        picks = order_by_proximity(picks, hotel_point)
        # keep the day realistic: drop the biggest detour while the route needs > 2.5 h of travel
        while len(picks) > 1 and _route_minutes(picks, hotel_point) > 150:
            worst = max(picks, key=lambda p: _route_minutes(picks, hotel_point)
                        - _route_minutes([q for q in picks if q is not p], hotel_point))
            picks.remove(worst)
            used.discard(worst["id"])

        prev: Optional[dict] = hotel_point
        lunch_done = not lunch_needed     # arriving after lunchtime: eaten on the way
        for p in picks:
            travel = travel_minutes(prev, p) if prev else 15
            would_end = cursor + travel + p.get("hours", 1.5) * 60
            if not lunch_done and cursor >= LUNCH_EARLIEST - 45 and (cursor >= LUNCH_EARLIEST or would_end > LUNCH_LATEST):
                cursor, prev = _add_meal(items, restaurants, "lunch", prev, used_rest, style, travelers, cursor,
                                     trip.get("price_index", 1.0))
                lunch_done = True
                travel = travel_minutes(prev, p) if prev else 15
            cursor += travel
            dur = int(p.get("hours", 1.5) * 60)
            cost = prices.get("tickets", {}).get(p["id"], (p.get("cost") or 0) * travelers)
            notes = p.get("swap_note") or p.get("reason") or p.get("description", "")
            items.append({"time": hhmm(cursor), "end": hhmm(cursor + dur), "type": "activity", "name": p["name"],
                          "place_id": p["id"], "cost": cost, "area": p.get("area"), "indoor": p.get("indoor"),
                          "tags": p.get("tags", []), "travel_min": travel, "notes": notes})
            cursor += dur
            prev = p
        if not lunch_done:
            cursor = max(cursor, LUNCH_EARLIEST)
            cursor, prev = _add_meal(items, restaurants, "lunch", prev, used_rest, style, travelers, cursor,
                                     trip.get("price_index", 1.0))

        if last and transport:
            items.append({"time": hhmm(DEPARTURE_AT), "end": hhmm(DEPARTURE_AT + int(transport["hours"] * 60)),
                          "type": "transport", "name": f"{transport['name']} back to {trip['origin']}",
                          "cost": transport["total"] - transport["total"] // 2, "notes": "Return journey"})
        else:
            if cursor > 21 * 60:
                items.append({"time": hhmm(cursor), "end": "—", "type": "free",
                              "name": "Check in and rest (meal on the way)", "cost": 0})
            elif DINNER_AT - cursor >= 75:
                items.append({"time": hhmm(cursor + 10), "end": hhmm(DINNER_AT - 15), "type": "free",
                              "name": "Check in and rest at the hotel" if first and hotel else
                              "Free time to explore nearby", "cost": 0})
            if cursor <= 21 * 60:
                cursor = max(cursor + 20, DINNER_AT)
                cursor, prev = _add_meal(items, restaurants, "dinner", prev, used_rest, style, travelers, cursor,
                                         trip.get("price_index", 1.0))
            evening = [c for c in attractions if "evening" in c.get("tags", []) and c["id"] not in used
                       and (c.get("indoor") or not bad) and travel_minutes(prev, c) <= 35]
            if evening and cursor <= 20 * 60 + 30 and (likes & {"nightlife", "view", "food", "beach"} or len(picks) < 3):
                ev = max(evening, key=lambda c: c.get("score", 0) - travel_minutes(prev, c) / 15)
                travel = travel_minutes(prev, ev)
                cursor += travel
                used.add(ev["id"])
                items.append({"time": hhmm(cursor), "end": hhmm(cursor + int(ev.get("hours", 1.5) * 60)),
                              "type": "activity", "name": ev["name"], "place_id": ev["id"],
                              "cost": prices.get("tickets", {}).get(ev["id"], (ev.get("cost") or 0) * travelers),
                              "area": ev.get("area"), "indoor": ev.get("indoor"), "tags": ev.get("tags", []),
                              "travel_min": travel, "notes": ev.get("reason") or "Evening activity"})
                cursor += int(ev.get("hours", 1.5) * 60)
            if hotel and i < days - 1:
                nightly = hotel.get("nightly") or [hotel["total"] // max(1, days - 1)] * days
                items.append({"time": hhmm(max(22 * 60, cursor + 20)), "end": "—", "type": "lodging", "name": f"Overnight at {hotel['name']}",
                              "cost": int(nightly[i]) if i < len(nightly) else int(nightly[-1]),
                              "notes": f"{hotel['rooms']} room(s)"})
        out_days.append({"day": i + 1, "date": date.isoformat(), "weekday": date.strftime("%a"), "arrive": arrive,
                         "weather": {"description": fc.get("description", "n/a"), "bad": bad,
                                     "tmax": fc.get("tmax"), "tmin": fc.get("tmin"),
                                     "rain_prob": fc.get("rain_prob")},
                         "items": items})
    return {"destination": trip["destination"], "days": out_days, "hotel": hotel, "transport": transport,
            "meal_style": style, "travelers": travelers, "currency": trip["currency"]}


def _add_meal(items: list, restaurants: list, meal: str, prev: Optional[dict], used_rest: set, style: str,
              travelers: int, cursor: int, price_index: float = 1.0) -> tuple[int, Optional[dict]]:
    r = pick_restaurant(restaurants, meal, prev, used_rest, style, price_index)
    if r is None:
        cost = (12000 if meal == "lunch" else 20000) if style != "budget" else (8000 if meal == "lunch" else 11000)
        cost = int(round(cost * price_index, -2))
        items.append({"time": hhmm(cursor), "end": hhmm(cursor + 60), "type": "meal", "meal": meal,
                      "name": f"{meal.title()} at a local restaurant", "cost": cost * travelers})
        return cursor + 60, prev
    travel = travel_minutes(prev, r) if prev else 10
    cursor += travel
    items.append({"time": hhmm(cursor), "end": hhmm(cursor + 60), "type": "meal", "meal": meal, "name": r["name"],
                  "place_id": r["id"], "cost": int(r.get("cost", 0)) * travelers, "area": r.get("area"),
                  "travel_min": travel, "notes": r.get("reason", "")})
    return cursor + 60, r
