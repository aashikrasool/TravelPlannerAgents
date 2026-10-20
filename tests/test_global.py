"""World-wide destinations, dropdown form input and direct agent-to-agent communication."""
from travel_planner import world
from travel_planner.agents import parsing
from travel_planner.config import settings
from travel_planner.runner import TravelPlanner, plan_trip
from travel_planner.tools import places as places_tool
from travel_planner.tools import weather as weather_tool

PEER = ("request", "reply", "notice", "ack")


def fake_osm(city, lat, lon, price_index=1.0, radius_m=9000):
    """Stand-in for the live OpenStreetMap lookup (tests run offline)."""
    sights = [("Grand Museum", ["history", "culture"], True, 15000), ("City Art Gallery", ["art"], True, 9000),
              ("Old Town Square", ["culture", "architecture"], False, 0), ("River Park", ["nature", "relax"], False, 0),
              ("Hill Viewpoint", ["view", "nature"], False, 0), ("Cathedral", ["history", "architecture"], False, 0),
              ("Food Market", ["food", "market"], False, 0), ("Castle", ["history"], False, 12000),
              ("Botanic Garden", ["nature"], False, 5000), ("Harbour Walk", ["view"], False, 0),
              ("Science Centre", ["family"], True, 14000), ("Night Bazaar", ["food", "evening"], False, 0)]
    out = [{"id": f"osm_{i}", "name": n, "kind": "attraction", "tags": t, "indoor": ind,
            "cost": int(c * price_index), "hours": 1.5, "lat": lat + i * 0.004, "lon": lon + (i % 3) * 0.004,
            "area": city, "popularity": 4, "source": "OpenStreetMap"} for i, (n, t, ind, c) in enumerate(sights)]
    out += [{"id": f"osm_r{i}", "name": f"Restaurant {i}", "kind": "restaurant", "tags": ["food"],
             "cost": int(15000 * price_index), "meal": "any", "indoor": True, "hours": 1.0,
             "lat": lat + 0.002 * i, "lon": lon, "area": city, "popularity": 3, "source": "OpenStreetMap"}
            for i in range(6)]
    return out


TOKYO_FORM = {"country": "JP", "city": "Tokyo", "origin_country": "KR", "origin_city": "Seoul",
              "start_date": "2026-12-04", "days": 3, "travelers": 2, "budget": 2_000_000, "currency": "KRW",
              "interests": ["food", "culture"], "avoid": ["hiking"], "style": "standard", "pace": "balanced"}


# ---------------------------------------------------------------- dropdown data

def test_world_dropdown_data():
    assert len(world.countries()) > 200
    assert "Asia" in world.continents() and "Europe" in world.continents()
    assert "Tokyo" in world.regions("JP")
    italy = world.cities("IT", limit=5)
    assert italy[0]["name"] == "Rome" and italy[0]["population"] >= italy[1]["population"]
    assert {c["region"] for c in world.cities("FR", "Ile-de-France", limit=20)} == {"Ile-de-France"}


def test_parse_world_cities_and_currencies():
    p = parsing.rule_based_parse("5 days in Tokyo from Lahore with 300,000 rupees, love food")
    assert (p["destination"], p["origin"], p["currency"]) == ("Tokyo", "Lahore", "PKR")
    p = parsing.rule_based_parse("A week in Paris for 2 people, 3000 euros, art, no nightlife")
    assert (p["destination"], p["days"], p["currency"], p["travelers"]) == ("Paris", 7, "EUR", 2)
    assert parsing.rule_based_parse("4 days in Jeju")["destination"] == "Jeju City"
    assert parsing.rule_based_parse("3 days with nice beaches")["destination"] is None   # 'nice' is not Nice


def test_money_conversion_round_trip():
    base = world.to_base(100, "EUR")
    assert abs(world.from_base(base, "EUR") - 100) < 0.5
    assert world.money(world.to_base(250, "USD"), "USD") == "250 USD"


# ---------------------------------------------------------------- form input, any country

def test_form_plan_for_tokyo_with_live_places(monkeypatch):
    monkeypatch.setattr(settings, "places_mode", "auto")
    monkeypatch.setattr(places_tool, "search_osm", fake_osm)
    s = plan_trip(form=TOKYO_FORM).state
    trip = s["trip"]
    assert (trip["destination"], trip["country"], trip["input_source"]) == ("Tokyo", "JP", "form")
    assert trip["origin"] == "Seoul" and trip["price_index"] == world.price_index("JP")
    assert s["tool_status"]["places"]["source"] == "OpenStreetMap (live)"
    transport = [i for d in s["itinerary"]["days"] for i in d["items"] if i["type"] == "transport"]
    assert len(transport) == 2 and "flight" in transport[0]["name"].lower()
    assert s["validation"]["passed"] and s["validation"]["total"] <= trip["budget_krw"]


def test_places_down_for_unknown_city_asks_then_uses_generic_plan():
    planner = TravelPlanner()
    form = dict(TOKYO_FORM, country="PT", city="Lisbon", origin_country=None, origin_city=None, currency="EUR",
                budget=900)
    res = planner.start(form=form)                       # offline: OpenStreetMap is not reachable in tests
    assert res.status == "needs_input" and res.question["options"][1]["id"] == "generic_plan"
    res = planner.resume(res.thread_id, "generic_plan")
    while res.status == "needs_input":
        res = planner.resume(res.thread_id, res.question["options"][0]["id"])
    assert res.state["trip"]["country"] == "PT" and res.state["itinerary"]["days"]
    assert "EUR" in res.state["summary"]


def test_long_haul_arrival_day_is_for_settling_in(monkeypatch):
    monkeypatch.setattr(settings, "places_mode", "auto")
    monkeypatch.setattr(places_tool, "search_osm", fake_osm)
    form = dict(TOKYO_FORM, country="FR", city="Paris", origin_country="KR", origin_city="Seoul", days=4,
                travelers=1, budget=3_500_000)
    s = plan_trip(form=form).state
    day1 = s["itinerary"]["days"][0]
    assert day1["arrive"] >= 16 * 60                       # ~12 h flight
    assert not [i for i in day1["items"] if i["type"] == "activity"]
    assert not any(i["type"] == "infeasible_schedule" for i in s["validation"]["issues"] if "Day 1" in i["message"])


# ---------------------------------------------------------------- direct agent-to-agent communication

def test_agents_talk_to_each_other_directly(monkeypatch):
    monkeypatch.setattr(settings, "places_mode", "auto")
    monkeypatch.setattr(places_tool, "search_osm", fake_osm)
    s = plan_trip(form=dict(TOKYO_FORM, budget=1_400_000)).state
    peer = [m for m in s["messages"] if m["kind"] in PEER]
    pairs = {(m["sender"], m["receiver"], m["task"]) for m in peer}
    assert ("research", "booking", "prefer_area") in pairs
    assert ("booking", "research", "prefer_area") in pairs              # the reply
    assert ("booking", "budget", "lodging_reranked") in pairs           # booking informs budget
    # every request got an answer that points back to it
    requests = [m for m in s["mailbox"] if m["kind"] == "request"]
    replies = {m["reply_to"] for m in s["mailbox"] if m["kind"] == "reply"}
    assert requests and all(r["id"] in replies for r in requests)
    assert all(m["status"] != "sent" for m in s["mailbox"])            # nothing left undelivered
    # hotels were ranked by distance because research asked for it
    assert all("distance_km" in h for h in s["lodging_options"] if h.get("lat") is not None)


def test_weather_asks_research_for_indoor_options(monkeypatch):
    monkeypatch.setattr(settings, "places_mode", "auto")
    monkeypatch.setattr(places_tool, "search_osm", fake_osm)
    rainy = lambda city, lat, lon, start, days, mode=None: (  # noqa: E731
        [dict(d, code=63, description="Rain", bad=True) for d in weather_tool.mock_forecast(city, start, days, lat)],
        "test forecast")
    monkeypatch.setattr(weather_tool, "get_forecast", rainy)
    s = plan_trip(form=dict(TOKYO_FORM, days=4)).state
    peer = [(m["sender"], m["receiver"], m["task"], m["kind"]) for m in s["messages"] if m["kind"] in PEER]
    assert ("weather", "research", "need_indoor_activities", "request") in peer
    assert ("research", "weather", "need_indoor_activities", "reply") in peer
    assert ("weather", "budget", "new_candidates", "notice") in peer      # chain continues to Budget
    for d in s["itinerary"]["days"][1:]:
        acts = [i for i in d["items"] if i["type"] == "activity"]
        assert all(a["indoor"] or "rain" in (a.get("notes") or "").lower() for a in acts)


def test_budget_asks_booking_for_cheaper_rooms():
    s = plan_trip("3 days in Busan with a tight budget of 140,000 KRW, I like views").state
    peer = {(m["sender"], m["receiver"], m["task"], m["kind"]) for m in s["messages"] if m["kind"] in PEER}
    assert ("budget", "booking", "find_cheaper_lodging", "request") in peer
    assert ("booking", "budget", "find_cheaper_lodging", "reply") in peer
    assert any(h["id"].startswith("low_") for h in s["lodging_options"])   # booking found dorm / capsule options
