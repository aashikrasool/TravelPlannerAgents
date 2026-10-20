"""Budget agent: prices every candidate, allocates the budget, picks lodging/transport, tracks spending."""
from __future__ import annotations

import datetime as dt

from .. import bus, world
from ..state import PlanState, message
from ..tools import booking as booking_tool

# Target share of the (post-intercity-transport) budget for each category, by travel style.
ALLOCATION = {
    "budget":   {"lodging": 0.32, "food": 0.32, "activities": 0.16, "local_transit": 0.10, "buffer": 0.10},
    "standard": {"lodging": 0.40, "food": 0.30, "activities": 0.15, "local_transit": 0.08, "buffer": 0.07},
    "comfort":  {"lodging": 0.50, "food": 0.27, "activities": 0.13, "local_transit": 0.06, "buffer": 0.04},
}


def choose_transport(options: list[dict], budget: int, constraints: dict) -> dict | None:
    if not options:
        return None
    if constraints.get("transport_id"):
        match = [o for o in options if o["id"] == constraints["transport_id"]]
        if match:
            return match[0]
    if constraints.get("cheapest_transport"):
        return min(options, key=lambda o: o["total"])
    # good value: not more than 35% of the budget, trading money against hours, else cheapest
    reasonable = [o for o in options if o["total"] <= 0.35 * budget]
    if reasonable:
        return min(reasonable, key=lambda o: o["total"] + o["hours"] * 6000 * max(1, o["total"] // 100000))
    return min(options, key=lambda o: o["total"])


def choose_hotel(options: list[dict], target: float, constraints: dict) -> dict | None:
    available = [h for h in options if h["available"]]
    if not available:
        return None
    if constraints.get("max_lodging_total") is not None:
        capped = [h for h in available if h["total"] <= constraints["max_lodging_total"]]
        available = capped or available
    under = [h for h in available if h["total"] <= target]
    if under:
        # best tier that fits; between equal tiers the one closest to the activities
        return max(under, key=lambda h: (h["tier"], -h.get("distance_km", 0), -h["total"]))
    return min(available, key=lambda h: (h["total"], h.get("distance_km", 0)))


def _style(trip: dict, prefs: dict, constraints: dict) -> str:
    if constraints.get("meal_style"):
        return constraints["meal_style"]
    if trip.get("style") == "budget":
        return "budget"
    return "foodie" if "food" in prefs.get("likes", []) or trip.get("style") == "comfort" else "normal"


def _price(state: dict) -> dict:
    """The Budget agent's core computation (also re-used by its message handlers)."""
    trip = state["trip"]
    prefs = state.get("preferences", {})
    constraints = state.get("constraints", {}) or {}
    budget = int(trip["budget_krw"])
    travelers, days = trip["travelers"], trip["days"]
    start = dt.date.fromisoformat(trip["start_date"])
    shares = ALLOCATION.get(trip.get("style", "standard"), ALLOCATION["standard"])

    transport = choose_transport(state.get("transport_options", []), budget, constraints)
    after_transport = max(0, budget - (transport["total"] if transport else 0))
    lodging_target = after_transport * shares["lodging"]
    hotel = choose_hotel(state.get("lodging_options", []), lodging_target, constraints)

    meal_style = _style(trip, prefs, constraints)
    tickets = {c["id"]: booking_tool.quote_ticket(c, start, travelers)
               for c in state.get("candidates", []) if c["kind"] == "attraction"}
    allocation = {k: int(after_transport * v) for k, v in shares.items()}
    if transport:
        allocation["intercity_transport"] = transport["total"]
    prices = {"tickets": tickets, "breakfast_per_day": booking_tool.breakfast(trip, meal_style) * travelers,
              "transit_per_day": booking_tool.local_transit(trip, days, travelers) // days,
              "meal_style": meal_style, "allocation": allocation, "lodging_target": int(lodging_target)}
    return {"prices": prices, "selection": {"hotel": hotel, "transport": transport}}


def budget_agent(state: PlanState) -> dict:
    trip = state["trip"]
    out = _price(state)
    prices, hotel, transport = out["prices"], out["selection"]["hotel"], out["selection"]["transport"]
    cur = trip["currency"]
    text = (f"Budget {world.money(trip['budget_krw'], cur)}: "
            + (f"transport {transport['name']} {world.money(transport['total'], cur)}; " if transport else "")
            + (f"hotel {hotel['name']} {world.money(hotel['total'], cur)} "
               f"(target {world.money(prices['lodging_target'], cur)}); " if hotel else "")
            + f"meal style '{prices['meal_style']}'.")
    msgs = [message("budget", "supervisor", "price_and_allocate",
                    {"budget_krw": trip["budget_krw"], "travelers": trip["travelers"], "days": trip["days"]},
                    {"hotel": hotel and hotel["name"], "transport": transport and transport["name"],
                     "meal_style": prices["meal_style"], "allocation": prices["allocation"]}, text=text)]

    mailbox = []
    nights = trip["days"] - 1
    lodging = [h for h in state.get("lodging_options", []) if h["available"]]
    cheapest = min((h["total"] for h in lodging), default=None)
    if nights > 0 and (cheapest is None or cheapest > prices["lodging_target"] * 1.05) \
            and not bus.already_asked(state, "budget", "booking", "find_cheaper_lodging"):
        why = (f"Transport alone uses the whole budget, so there is nothing left for lodging"
               if prices["lodging_target"] <= 0 else
               f"The cheapest available room ({world.money(cheapest, cur) if cheapest else 'none'}) is above my "
               f"lodging target of {world.money(prices['lodging_target'], cur)}")
        mailbox.append(bus.request(
            "budget", "booking", "find_cheaper_lodging", {"max_total": prices["lodging_target"], "nights": nights},
            why + ". Can you find cheaper places to stay?"))

    cap = prices["allocation"]["activities"] / max(1, trip["days"]) / trip["travelers"] / 2
    pricey = [c for c in state.get("candidates", []) if c["kind"] == "attraction" and (c.get("cost") or 0) > cap]
    if pricey and not bus.already_asked(state, "budget", "research", "flag_expensive"):
        mailbox.append(bus.request(
            "budget", "research", "flag_expensive", {"cap_per_person": int(cap)},
            (f"The activity budget is about {world.money(cap, cur)} per person per activity. " if cap >= 1 else
             "There is no money left for paid activities. ")
            + f"{len(pricey)} candidates cost more - please prefer cheaper or free options."))
    return {**out, "messages": msgs + mailbox, "mailbox": mailbox}


# ---------------------------------------------------------------- peer messages handled by Budget

def _reselect(state: dict, why: str) -> dict:
    old = (state.get("selection") or {}).get("hotel")
    out = _price(state)
    new = out["selection"]["hotel"]
    cur = state["trip"]["currency"]
    if new and (not old or new["id"] != old["id"]):
        text = f"{why}: switched hotel to {new['name']} ({world.money(new['total'], cur)})."
    else:
        text = f"{why}: kept {new['name'] if new else 'no hotel'}."
    return {"updates": out, "result": {"hotel": new and new["name"]}, "text": text}


@bus.handles("budget", "lodging_reranked")
def handle_reranked(state: dict, msg: dict) -> dict:
    return _reselect(state, "Re-checked the hotel after Booking ranked by distance")


@bus.handles("budget", "new_candidates")
def handle_new_candidates(state: dict, msg: dict) -> dict:
    out = _price(state)
    return {"updates": {"prices": out["prices"]}, "result": {"priced": len(out["prices"]["tickets"])},
            "text": f"Priced the {len(msg['input'].get('names', []))} new indoor options."}


@bus.on_reply("budget", "find_cheaper_lodging")
def on_cheaper_reply(state: dict, reply: dict) -> dict:
    res = _reselect(state, "Booking found cheaper lodging")
    log = message("budget", "supervisor", "lodging_decision", result=res["result"], text=res["text"])
    return {"updates": res["updates"], "followups": [dict(log, status="delivered")]}


def tally(itinerary: dict) -> dict:
    """Walk the itinerary in order; add running (cumulative) totals and a category breakdown."""
    categories = {"transport": 0, "lodging": 0, "food": 0, "activities": 0, "local_transit": 0}
    cat_of = {"transport": "transport", "lodging": "lodging", "meal": "food", "activity": "activities",
              "transit": "local_transit"}
    running = 0
    for day in itinerary.get("days", []):
        day_total = 0
        for item in day["items"]:
            cost = int(item.get("cost") or 0)
            running += cost
            day_total += cost
            item["cumulative"] = running
            categories[cat_of.get(item["type"], "activities")] += cost
        day["day_total"] = day_total
        day["cumulative"] = running
    return {"total": running, "by_category": categories}
