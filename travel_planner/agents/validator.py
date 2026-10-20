"""Validation + budget check, human-in-the-loop review, and targeted replanning."""
from __future__ import annotations

from langgraph.types import interrupt

from .. import world
from ..state import PlanState, message
from .booking_agent import booking_agent
from .budget import budget_agent
from .research import research_agent
from .scheduler import hhmm  # noqa: F401  (re-exported for UI helpers)
from .weather_agent import weather_agent

BLOCKING = {"over_budget", "no_candidates", "incomplete"}


def _minutes(t: str) -> int | None:
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def check_itinerary(state: PlanState) -> dict:
    """Pure check used by the validator node and by the evaluation harness."""
    trip, it = state["trip"], state.get("itinerary") or {}
    issues: list[dict] = []
    total = it.get("totals", {}).get("total", 0)
    budget = trip["budget_krw"]
    cur = trip.get("currency", "KRW")
    accepted_over = any(d.get("type") == "accept_over_budget" for d in state.get("decisions", []))
    accepted_light = any(d.get("type") == "accept_as_is" for d in state.get("decisions", []))

    if not [c for c in state.get("candidates", []) if c["kind"] == "attraction"]:
        issues.append({"type": "no_candidates", "severity": "blocking",
                       "message": "The places service failed, so there are no activities to plan with."})
    if total > budget and not accepted_over:
        issues.append({"type": "over_budget", "severity": "blocking", "amount": total - budget,
                       "message": f"Draft costs {world.money(total, cur)}, {world.money(total - budget, cur)} over "
                                  f"the {world.money(budget, cur)} budget."})

    completeness = {"days": 0, "with_activity": 0, "with_lunch": 0, "with_dinner": 0, "lodging_ok": True}
    feasible_days = 0
    for day in it.get("days", []):
        items = day["items"]
        completeness["days"] += 1
        acts = [i for i in items if i["type"] == "activity"]
        has_lunch = any(i.get("meal") == "lunch" for i in items)
        has_dinner = any(i.get("meal") == "dinner" for i in items)
        departs = any(i["type"] == "transport" and "back to" in i["name"] for i in items)
        arrive = day.get("arrive")               # long journeys: the arrival day is for settling in
        if arrive is not None:
            has_lunch = has_lunch or arrive > 13 * 60 + 30
            has_dinner = has_dinner or arrive > 20 * 60 + 15
            acts = acts or ([{"arrival": True}] if arrive >= 15 * 60 else [])
        completeness["with_activity"] += bool(acts)
        completeness["with_lunch"] += has_lunch
        completeness["with_dinner"] += has_dinner or departs
        if not acts or not has_lunch or not (has_dinner or departs):
            issues.append({"type": "incomplete", "severity": "warning" if accepted_light else "blocking",
                           "message": f"Day {day['day']} is missing an activity or a meal."})
        # feasibility: no overlapping slots, travel time and finish time are realistic
        timed = [(_minutes(i["time"]), _minutes(i["end"])) for i in items
                 if _minutes(i["time"]) is not None and _minutes(i.get("end")) is not None
                 and i["type"] != "lodging"]
        overlap = any(timed[k + 1][0] < timed[k][1] for k in range(len(timed) - 1))
        travel = sum(i.get("travel_min", 0) for i in items)
        last_end = max((e for _, e in timed), default=0)
        ok = not overlap and travel <= 180 and last_end <= 23 * 60
        feasible_days += ok
        if not ok:
            issues.append({"type": "infeasible_schedule", "severity": "warning",
                           "message": f"Day {day['day']}: tight schedule (travel {travel} min, ends {last_end // 60}:"
                                      f"{last_end % 60:02d}{', overlapping slots' if overlap else ''})."})
    if trip["days"] > 1 and not it.get("hotel"):
        completeness["lodging_ok"] = False
        issues.append({"type": "incomplete", "severity": "blocking",
                       "message": "No available accommodation was found."})

    for tool, st in (state.get("tool_status") or {}).items():
        if st.get("status") == "degraded":
            issues.append({"type": "tool_degraded", "severity": "warning",
                           "message": f"{tool} API unavailable - using {st.get('source')}."})
        elif st.get("status") == "recovered":
            issues.append({"type": "tool_recovered", "severity": "info",
                           "message": f"{tool} API failed once and recovered on retry."})

    blocking = [i for i in issues if i["severity"] == "blocking"]
    return {"passed": not blocking, "issues": issues, "total": total, "budget": budget,
            "remaining": budget - total, "completeness": completeness,
            "feasible_days": feasible_days, "n_days": len(it.get("days", []))}


def validate(state: PlanState) -> dict:
    v = check_itinerary(state)
    return {"validation": v,
            "messages": [message("validator", "supervisor", "validate_budget_and_schedule",
                                 {"budget": v["budget"]},
                                 {"passed": v["passed"], "total": v["total"],
                                  "issues": [i["message"] for i in v["issues"]]},
                                 "ok" if v["passed"] else "failed",
                                 text=("Plan passes the budget, completeness and schedule checks." if v["passed"] else
                                       "Plan fails: " + "; ".join(i["message"] for i in v["issues"]
                                                                  if i["severity"] == "blocking")))]}


# --- human in the loop -----------------------------------------------------------------------

def build_options(state: PlanState) -> list[dict]:
    """Concrete choices for the user, each with what it saves and which agents it re-runs."""
    v = state["validation"]
    cur = state["trip"]["currency"]
    issues = {i["type"] for i in v["issues"] if i["severity"] == "blocking"}
    options: list[dict] = []
    if "no_candidates" in issues:
        options.append({"id": "retry_places", "type": "retry_places", "label": "Retry the places service",
                        "rerun": ["research", "weather", "budget"]})
        options.append({"id": "generic_plan", "type": "generic_plan",
                        "label": "Continue with a generic plan (placeholder activities)",
                        "rerun": ["research", "weather", "budget"]})
        return options
    if "incomplete" in issues and "over_budget" not in issues and not state.get("itinerary", {}).get("hotel"):
        options.append({"id": "retry_booking", "type": "retry_booking", "label": "Search accommodation again",
                        "rerun": ["booking", "budget"]})
    if "over_budget" in issues:
        it = state["itinerary"]
        paid = [i for d in it["days"] for i in d["items"] if i["type"] == "activity" and i.get("cost", 0) > 0]
        for item in sorted(paid, key=lambda i: -i["cost"])[:4]:
            options.append({"id": f"drop:{item['place_id']}", "type": "drop_activity", "place_id": item["place_id"],
                            "label": f"Drop {item['name']}", "saves": item["cost"], "rerun": []})
        hotel = it.get("hotel")
        cheaper = sorted([h for h in state.get("lodging_options", []) if h["available"] and hotel
                          and h["total"] < hotel["total"]], key=lambda h: -h["total"])
        if cheaper:
            h = cheaper[0]
            options.append({"id": "cheaper_hotel", "type": "cheaper_hotel", "max_total": h["total"],
                            "label": f"Switch to {h['name']} ({world.money(h['total'], cur)} instead of "
                                     f"{world.money(hotel['total'], cur)})",
                            "saves": hotel["total"] - h["total"], "rerun": ["booking", "budget"]})
        if it.get("meal_style") != "budget":
            meals = sum(i["cost"] for d in it["days"] for i in d["items"] if i["type"] == "meal")
            options.append({"id": "budget_meals", "type": "budget_meals", "label": "Choose cheaper local meals",
                            "saves": int(meals * 0.35), "rerun": ["budget"]})
        tr = it.get("transport")
        cheaper_tr = [t for t in state.get("transport_options", []) if tr and t["total"] < tr["total"]]
        if cheaper_tr:
            t = min(cheaper_tr, key=lambda t: t["total"])
            options.append({"id": "cheaper_transport", "type": "cheaper_transport", "transport_id": t["id"],
                            "label": f"Take the {t['name']} instead ({t['hours']} h)",
                            "saves": tr["total"] - t["total"], "rerun": ["budget"]})
        options.append({"id": "accept_over_budget", "type": "accept_over_budget",
                        "label": "Keep this plan and go over budget", "saves": 0, "rerun": []})
    for o in options:
        if o.get("saves"):
            o["saves_text"] = world.money(o["saves"], cur)
    if "incomplete" in issues:
        options.append({"id": "accept_as_is", "type": "accept_as_is",
                        "label": "Accept a lighter schedule on that day", "saves": 0, "rerun": []})
    return options


def auto_choose(state: PlanState, options: list[dict]) -> dict:
    """Simulated user for evaluation runs: fix the problem with the least loss of preference."""
    tried = {d["type"] for d in state.get("decisions", [])}
    if any(o["type"] == "retry_places" for o in options):
        return next(o for o in options if o["type"] == ("generic_plan" if "retry_places" in tried else "retry_places"))
    gap = state["validation"].get("total", 0) - state["validation"].get("budget", 0)
    likes = set(state.get("preferences", {}).get("likes", []))
    scores = {c["id"]: c.get("score", 3) for c in state.get("candidates", [])}

    def pain(o: dict) -> float:
        if o["type"] == "drop_activity":
            return 1.0 + scores.get(o["place_id"], 3) / 3
        if o["type"] == "budget_meals":
            return 5.0 if "food" in likes else 1.5
        if o["type"] == "cheaper_hotel":
            return 2.0
        if o["type"] == "cheaper_transport":
            return 1.5
        return 100.0

    if gap <= 0:   # not a budget problem
        return next((o for o in options if o["type"] in ("retry_booking", "accept_as_is")), options[0])
    usable = [o for o in options if o["type"] in ("drop_activity", "budget_meals", "cheaper_hotel",
                                                  "cheaper_transport") and o.get("saves", 0) > 0]
    if not usable:
        return next((o for o in options if o["type"] == "accept_over_budget"), options[0])
    covering = [o for o in usable if o.get("saves", 0) >= gap]
    if covering:
        return min(covering, key=pain)
    return max(usable, key=lambda o: o.get("saves", 0) / pain(o))


def human_review(state: PlanState) -> dict:
    v = state["validation"]
    options = build_options(state)
    question = {"issues": [i["message"] for i in v["issues"] if i["severity"] == "blocking"],
                "total": v["total"], "budget": v["budget"], "options": options}
    if state.get("auto_mode"):
        choice = auto_choose(state, options)
    else:
        answer = interrupt(question)          # pauses the graph until the user answers
        answer_id = answer.get("id") if isinstance(answer, dict) else answer
        choice = next((o for o in options if o["id"] == answer_id), None) or auto_choose(state, options)
    return {"decisions": [choice], "pending_question": question,
            "messages": [message("user", "supervisor", "resolve_issue", question["issues"],
                                 {"choice": choice["label"]}, "ok", kind="reply",
                                 text=f"Traveller chose: {choice['label']}"
                                      + (" (automatic)" if state.get("auto_mode") else ""))]}


def replan(state: PlanState) -> dict:
    """Apply the user's choice to the constraints and re-run only the affected agents."""
    choice = state["decisions"][-1]
    constraints = dict(state.get("constraints") or {})
    t = choice["type"]
    if t == "drop_activity":
        constraints["excluded_ids"] = sorted(set(constraints.get("excluded_ids", [])) | {choice["place_id"]})
        constraints["prefer_free"] = True     # replacements should not cost more than what was dropped
    elif t == "cheaper_hotel":
        constraints["max_lodging_total"] = choice["max_total"]
    elif t == "budget_meals":
        constraints["meal_style"] = "budget"
    elif t == "cheaper_transport":
        constraints["transport_id"] = choice["transport_id"]
    elif t == "generic_plan":
        constraints["allow_generic"] = True

    working: dict = dict(state)
    working["constraints"] = constraints
    updates: dict = {"constraints": constraints, "iterations": state.get("iterations", 0) + 1, "messages": [],
                     "mailbox": [], "tool_status": {}, "inject_failures": {}}
    agents = {"research": research_agent, "weather": weather_agent, "booking": booking_agent, "budget": budget_agent}
    rerun = choice.get("rerun", [])
    updates["messages"].append(message("supervisor", "supervisor", "targeted_replan", {"choice": choice["label"]},
                                       {"rerun_agents": rerun or ["merge only"]}, kind="task",
                                       text=f"Applying '{choice['label']}': re-running "
                                            + (", ".join(rerun) if rerun else "only the merge step") + "."))
    for name in ("research", "weather", "booking", "budget"):   # dependency order
        if name not in rerun:
            continue
        out = agents[name](working)
        for k, val in out.items():
            if k in ("messages", "mailbox"):
                updates[k] += val
                if k == "mailbox":
                    working["mailbox"] = (working.get("mailbox") or []) + val
            elif k in ("tool_status", "inject_failures"):
                updates[k].update(val)
                working[k] = {**(working.get(k) or {}), **val}
            else:
                updates[k] = val
                working[k] = val
    return updates
