import json

from travel_planner.agents import parsing
from travel_planner.agents.validator import check_itinerary
from travel_planner.baseline import run_baseline
from travel_planner.llm import LLMClient, extract_json, set_llm
from travel_planner.runner import TravelPlanner, plan_trip

BUSAN = "Plan 3 days in Busan with a KRW 300,000 budget. I love food but avoid hiking."
TIGHT = "3 days in Busan with a tight budget of 180,000 KRW, I like views and culture."


# ---------------------------------------------------------------- parsing
def test_parse_request():
    p = parsing.rule_based_parse(BUSAN)
    assert p["destination"] == "Busan" and p["days"] == 3
    assert p["budget"] == 300000 and p["currency"] == "KRW"
    assert p["likes"] == ["food"] and p["dislikes"] == ["hiking"]


def test_parse_variants():
    p = parsing.rule_based_parse("2 nights in Seoul from Busan for 2 people, budget $600, no shopping")
    assert (p["destination"], p["origin"], p["days"], p["travelers"]) == ("Seoul", "Busan", 3, 2)
    assert p["currency"] == "USD" and "shopping" in p["dislikes"]
    assert parsing.to_krw(600, "USD") == 828000


def test_extract_json_tolerates_fences():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('result: {"b": [1, 2]} done') == {"b": [1, 2]}


# ---------------------------------------------------------------- end to end
def test_plan_is_valid_and_respects_preferences():
    res = plan_trip(BUSAN)
    s = res.state
    assert res.status == "done" and s["validation"]["passed"]
    assert s["validation"]["total"] <= 300000
    days = s["itinerary"]["days"]
    assert len(days) == 3
    tags = {c["id"]: set(c["tags"]) for c in s["candidates"]}
    for d in days:
        acts = [i for i in d["items"] if i["type"] == "activity"]
        assert acts, "every day has an activity"
        assert any(i.get("meal") == "lunch" for i in d["items"])
        assert not any("hiking" in tags.get(a["place_id"], set()) for a in acts), "disliked hiking never planned"
    # cumulative spending is tracked item by item
    last = days[-1]["items"][-1]["cumulative"]
    assert last == s["itinerary"]["totals"]["total"]


def test_all_agents_communicate_with_structured_messages():
    s = plan_trip(BUSAN).state
    senders = {m["sender"] for m in s["messages"]}
    assert {"supervisor", "research", "weather", "budget", "booking", "memory", "validator"} <= senders
    for m in s["messages"]:
        assert set(m) >= {"task", "input", "result", "status", "error"}
    # supervisor delegated tasks before specialists answered
    sent = [m for m in s["messages"] if m["status"] == "sent"]
    assert {m["receiver"] for m in sent} == {"research", "weather", "budget", "booking"}


def test_weather_swaps_outdoor_plans_on_rainy_days():
    s = plan_trip(BUSAN).state
    for d in s["itinerary"]["days"]:
        if d["weather"]["bad"]:
            acts = [i for i in d["items"] if i["type"] == "activity"]
            assert all(a["indoor"] or "rain" in (a.get("notes") or "").lower() for a in acts)


def test_human_in_the_loop_over_budget_and_targeted_replan():
    planner = TravelPlanner()
    res = planner.start(TIGHT)
    assert res.status == "needs_input"
    q = res.question
    assert "over" in q["issues"][0]
    meals = next(o for o in q["options"] if o["type"] == "budget_meals")
    assert meals["rerun"] == ["budget"]           # only the affected agent re-runs
    res = planner.resume(res.thread_id, meals["id"])
    while res.status == "needs_input":
        opt = next(o for o in res.question["options"] if o["type"] != "accept_over_budget")
        res = planner.resume(res.thread_id, opt["id"])
    s = res.state
    assert res.status == "done" and s["validation"]["total"] <= 180000
    replans = [m for m in s["messages"] if m["task"] == "targeted_replan"]
    assert replans and "research" not in replans[0]["result"]["rerun_agents"]


def test_transient_failure_recovers_with_retry():
    s = plan_trip(BUSAN, inject_failures={"weather": 1}).state
    assert s["tool_status"]["weather"]["status"] == "recovered"
    assert s["validation"]["passed"]


def test_api_down_degrades_gracefully():
    s = plan_trip(BUSAN, inject_failures={"weather": 99, "booking": 99}).state
    assert s["tool_status"]["weather"]["status"] == "degraded"
    assert s["tool_status"]["booking"]["status"] == "degraded"
    assert s["validation"]["passed"]


def test_places_down_asks_user_then_uses_generic_plan():
    planner = TravelPlanner()
    res = planner.start(BUSAN, inject_failures={"places": 99})
    assert res.status == "needs_input"
    ids = [o["id"] for o in res.question["options"]]
    assert ids == ["retry_places", "generic_plan"]
    res = planner.resume(res.thread_id, "generic_plan")
    while res.status == "needs_input":
        res = planner.resume(res.thread_id, res.question["options"][0]["id"])
    assert res.state["itinerary"]["days"]
    assert res.state["tool_status"]["places"]["status"] == "degraded"


def test_memory_persists_preferences_across_sessions(fresh_memory):
    plan_trip(BUSAN, user_id="alice")
    assert fresh_memory.get_preferences("alice")["likes"] == ["food"]
    s = plan_trip("2 days in Seoul with 300,000 won", user_id="alice").state
    assert "food" in s["preferences"]["likes"] and "hiking" in s["preferences"]["dislikes"]
    assert set(s["preferences"]["from_memory"]) == {"food", "hiking"}


def test_intercity_transport_included():
    s = plan_trip("3 days in Gyeongju from Seoul, budget $350, interested in history").state
    items = [i for d in s["itinerary"]["days"] for i in d["items"] if i["type"] == "transport"]
    assert len(items) == 2 and s["validation"]["passed"]


def test_missing_destination():
    res = plan_trip("I want a cheap holiday")
    assert res.status == "failed" and "destination" in res.state["summary"]


def test_baseline_fails_when_api_down_but_multi_agent_recovers():
    b = run_baseline(BUSAN, inject_failures={"booking": 99})
    assert b["status"] == "failed"
    assert plan_trip(BUSAN, inject_failures={"booking": 99}).state["validation"]["passed"]


def test_check_itinerary_detects_over_budget():
    s = plan_trip(BUSAN).state
    s = dict(s, trip=dict(s["trip"], budget_krw=100000), decisions=[])
    v = check_itinerary(s)
    assert not v["passed"] and any(i["type"] == "over_budget" for i in v["issues"])


# ---------------------------------------------------------------- LLM path (fake open-source model)
class FakeLLM(LLMClient):
    name, model = "fake", "fake-7b"

    def __init__(self):
        self.calls = []

    def chat(self, system, user, json_mode=False):
        self.calls.append(system[:40])
        if "Extract the trip request" in system:
            return json.dumps({"destination": "Busan", "days": 3, "budget": 300000, "currency": "KRW",
                               "likes": ["food", "made_up_tag"], "dislikes": ["hiking"]})
        if "Research agent" in system:
            return '```json\n{"ratings": [{"id": "bs_jagalchi", "fit": 10, "reason": "Seafood heaven"},' \
                   ' {"id": "not_a_real_id", "fit": 10, "reason": "hallucinated"}]}\n```'
        return "A delicious, rain-proof trip to Busan within budget."


def test_llm_agents_are_used_and_validated():
    fake = FakeLLM()
    set_llm(fake)
    s = plan_trip("I'd like to eat my way through Busan for three days, about 300k won, no hikes").state
    assert s["trip"]["parsed_with_llm"]
    assert "made_up_tag" not in s["preferences"]["likes"]                      # vocabulary enforced
    jag = next(c for c in s["candidates"] if c["id"] == "bs_jagalchi")
    assert jag["reason"] == "Seafood heaven"                                      # LLM rating applied
    assert all(c["id"] != "not_a_real_id" for c in s["candidates"])              # hallucination ignored
    assert s["summary"].startswith("A delicious")
    assert len(fake.calls) == 3
