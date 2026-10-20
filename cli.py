"""Command-line interface.

    python cli.py                         # interactive menus: continent > country > region > city > ...
    python cli.py "4 days in Lisbon from Madrid, 1200 EUR, food and views"   # free text
    python cli.py --auto --fail weather=99 "..."   # answer questions automatically, simulate an outage
    python cli.py --show-messages "..."   # print the full agent conversation
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from travel_planner import world
from travel_planner.estimates import typical_budget
from travel_planner.llm import describe_llm
from travel_planner.options import INTERESTS, PACES, STYLES
from travel_planner.runner import TravelPlanner

try:
    import questionary
except ImportError:   # plain numbered menus still work without it
    questionary = None

ICON = {"supervisor": "[SUP]", "research": "[RES]", "weather": "[WEA]", "budget": "[BUD]", "booking": "[BOK]",
        "memory": "[MEM]", "validator": "[VAL]", "user": "[YOU]"}


# ---------------------------------------------------------------- menu helpers (questionary or plain input)

def _interactive() -> bool:
    return questionary is not None and sys.stdin.isatty()


def choose(title: str, options: list[str], default: str | None = None, search: bool = False) -> str:
    if _interactive():
        if search and len(options) > 15:
            ans = questionary.autocomplete(f"{title} (type to search, Tab to complete):", choices=options,
                                           default=default or "", match_middle=True,
                                           validate=lambda v: v in options or "Pick an item from the list").ask()
        else:
            ans = questionary.select(title, choices=options, default=default).ask()
        if ans is None:
            raise KeyboardInterrupt
        return ans
    shown = options if len(options) <= 40 else options[:40]
    print(f"\n{title}")
    for i, o in enumerate(shown, 1):
        print(f"  {i:>2}. {o}")
    if len(options) > len(shown):
        print(f"  ... or type a name ({len(options)} options)")
    while True:
        raw = input(f"Choose 1-{len(shown)}{' or a name' if len(options) > len(shown) else ''}"
                    f"{f' [{default}]' if default else ''} > ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(shown):
            return shown[int(raw) - 1]
        low = raw.lower()
        exact = [o for o in options if o.lower() == low]
        match = exact or [o for o in options if o.lower().startswith(low)] or [o for o in options if low in o.lower()]
        if len(match) == 1 or (match and exact):
            return match[0]
        if match:
            return choose(f"Several match '{raw}' - which one?", match[:40])
        print("  Not found, try again.")


def choose_many(title: str, options: dict[str, str], default: list[str] | None = None) -> list[str]:
    keys = list(options)
    if _interactive():
        ans = questionary.checkbox(title + " (space to select, enter to confirm):",
                                   choices=[questionary.Choice(options[k], value=k, checked=k in (default or []))
                                            for k in keys]).ask()
        if ans is None:
            raise KeyboardInterrupt
        return ans
    print(f"\n{title}")
    for i, k in enumerate(keys, 1):
        print(f"  {i:>2}. {options[k]}")
    raw = input("Numbers separated by commas (empty = none) > ").strip()
    return [keys[int(x) - 1] for x in raw.replace(" ", "").split(",") if x.isdigit() and 0 < int(x) <= len(keys)]


def ask(title: str, default: str, validate=None) -> str:
    if _interactive():
        ans = questionary.text(title, default=default, validate=validate or (lambda v: True)).ask()
        if ans is None:
            raise KeyboardInterrupt
        return ans
    while True:
        raw = input(f"{title} [{default}] > ").strip() or default
        if validate is None or validate(raw) is True:
            return raw
        print("  Invalid value, try again.")


def pick_city(label: str, optional: bool = False) -> dict | None:
    continents = ["All continents"] + world.continents()
    cont = choose(f"{label} - continent", continents, default="All continents")
    countries = world.countries(None if cont == "All continents" else cont)
    names = [c["name"] for c in countries]
    if optional:
        names = ["(skip - no transport needed)"] + names
    name = choose(f"{label} - country", names, search=True)
    if name.startswith("(skip"):
        return None
    ctry = countries[[c["name"] for c in countries].index(name)]
    regions = ["All regions"] + world.regions(ctry["iso"])
    region = choose(f"{label} - region / state / province", regions, default="All regions", search=True)
    cities = world.cities(ctry["iso"], None if region == "All regions" else region, limit=300)
    labels = [f"{c['name']} ({c['region']})" for c in cities]
    city = cities[labels.index(choose(f"{label} - city (largest first)", labels, search=True))]
    return city


def _num(v: str) -> bool | str:
    try:
        return float(v.replace(",", "")) > 0 or "Must be positive"
    except ValueError:
        return "Enter a number"


def _date(v: str) -> bool | str:
    try:
        return dt.date.fromisoformat(v) >= dt.date.today() or "Date is in the past"
    except ValueError:
        return "Use YYYY-MM-DD"


def menu_form() -> dict:
    print("\nPlan a trip - answer a few questions (Ctrl+C to quit).")
    dest = pick_city("Destination")
    origin = pick_city("Travelling from", optional=True)
    start = ask("Start date (YYYY-MM-DD)", (dt.date.today() + dt.timedelta(days=7)).isoformat(), _date)
    days = int(choose("Number of days", [str(i) for i in range(1, 15)], default="3"))
    travelers = int(choose("Number of travellers", [str(i) for i in range(1, 11)], default="1"))
    home_cur = world.country(origin["country"])["currency"] if origin else world.country(dest["country"])["currency"]
    currencies = world.currencies()
    currency = choose("Currency", currencies, default=home_cur if home_cur in currencies else "USD", search=True)
    style = choose("Travel style", list(STYLES.values()), default=STYLES["standard"])
    style_key = next(k for k, v in STYLES.items() if v == style)
    est = typical_budget(dest, origin, days, travelers, style_key)
    typical = world.from_base(est["total"], currency)
    hint = f"typical: {typical:,.0f}" + (f", incl. ~{world.money(est['transport'], currency)} transport"
                                         if est["transport"] else "")
    budget = float(ask(f"Total budget in {currency} ({hint})", f"{round(typical, -1):.0f}", _num).replace(",", ""))
    interests = choose_many("Interests", INTERESTS, default=["food", "culture"])
    avoid = choose_many("Anything to avoid?", {k: v for k, v in INTERESTS.items() if k not in interests})
    pace = choose("Pace", list(PACES.values()), default=PACES["balanced"])
    notes = ask("Anything else? (optional)", "")
    return {"country": dest["country"], "region": dest["region"], "city": dest["name"],
            "origin_country": origin["country"] if origin else None, "origin_city": origin["name"] if origin else None,
            "start_date": start, "days": days, "travelers": travelers, "budget": budget, "currency": currency,
            "interests": interests, "avoid": avoid, "notes": notes,
            "style": style_key,
            "pace": next(k for k, v in PACES.items() if v == pace)}


# ---------------------------------------------------------------- output

def print_plan(state: dict, show_messages: bool) -> None:
    it, val, trip = state.get("itinerary"), state.get("validation", {}), state.get("trip", {})
    if not it:
        print(state.get("summary", "No plan produced."))
        return
    cur = trip["currency"]
    money = lambda v: world.money(v, cur)  # noqa: E731
    print("\n" + "=" * 86)
    print(f" {trip['days']}-day trip to {trip['destination']}, {trip['country_name']}  |  budget "
          f"{money(trip['budget_krw'])}  |  travellers {trip['travelers']}  |  {trip['style']}, {trip['pace']}")
    if trip.get("assumptions"):
        print(" Assumptions: " + "; ".join(trip["assumptions"]))
    print("=" * 86)
    for d in it["days"]:
        w = d["weather"]
        print(f"\nDay {d['day']} - {d['weekday']} {d['date']} - {w['description']}"
              f"{' (bad-weather plan)' if w['bad'] else ''}")
        for i in d["items"]:
            cost = money(i["cost"]) if i.get("cost") else "free"
            note = f"  <- {i['notes']}" if i.get("notes") and i["type"] == "activity" else ""
            print(f"  {i['time']:>5}  {i['name'][:44]:<44} {cost:>14}  (total {money(i.get('cumulative', 0))}){note}")
    t = it["totals"]
    print("\nBudget breakdown: " + ", ".join(f"{k.replace('_', ' ')} {money(v)}" for k, v in t["by_category"].items()))
    print(f"TOTAL {money(val.get('total', t['total']))} / budget {money(trip['budget_krw'])} -> "
          f"{'within budget' if val.get('total', 0) <= trip['budget_krw'] else 'OVER budget'}")
    for issue in val.get("issues", []):
        print(f"  [{issue['severity']}] {issue['message']}")
    print("\n" + state.get("summary", ""))

    peer = [m for m in state.get("messages", []) if m["kind"] in ("request", "reply", "notice", "ack")]
    print(f"\nAgent-to-agent conversation ({len(peer)} messages):" if not show_messages else "\nFull agent conversation:")
    for m in state.get("messages", []) if show_messages else peer:
        print(f"  {ICON.get(m['sender'], m['sender'])} -> {ICON.get(m['receiver'], m['receiver'])} "
              f"{m['kind']:<7} {m.get('text') or m['task']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Budget-aware multi-agent travel planner")
    ap.add_argument("request", nargs="?", help="trip request in your own words (omit for interactive menus)")
    ap.add_argument("--user", default="default", help="user id for preference memory")
    ap.add_argument("--auto", action="store_true", help="answer the planner's questions automatically")
    ap.add_argument("--fail", action="append", default=[],
                    help="inject API failures, e.g. --fail weather=99 (99 = API down, 1 = transient)")
    ap.add_argument("--show-messages", action="store_true", help="print every message, not only agent-to-agent")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    failures = {f.split("=")[0]: int(f.split("=")[1]) for f in args.fail}
    print(f"Model: {describe_llm()}")
    planner = TravelPlanner()
    try:
        if args.request:
            res = planner.start(args.request, user_id=args.user, auto=args.auto, inject_failures=failures)
        else:
            res = planner.start(user_id=args.user, auto=args.auto, inject_failures=failures, form=menu_form())
        while res.status == "needs_input":
            q = res.question
            print("\nThe planner needs your decision:")
            for issue in q["issues"]:
                print(f"  ! {issue}")
            labels = [o["label"] + (f"  (saves {o['saves_text']})" if o.get("saves_text") else "") for o in q["options"]]
            picked = choose("Which option should the agents apply?", labels)
            res = planner.resume(res.thread_id, q["options"][labels.index(picked)]["id"])
    except KeyboardInterrupt:
        print("\nCancelled.")
        return
    print_plan(res.state, args.show_messages)


if __name__ == "__main__":
    main()
