"""Streamlit web app.   Run:  streamlit run app.py"""
from __future__ import annotations

import datetime as dt
import json
import os

import pandas as pd
import streamlit as st

# On Streamlit Community Cloud, settings (e.g. the Groq API key) live in the app's private "Secrets" box.
# Copy them into environment variables BEFORE the planner reads its configuration.
try:
    for _key, _value in st.secrets.items():
        if isinstance(_value, (str, int, float, bool)):
            os.environ.setdefault(_key, str(_value))
except Exception:  # noqa: BLE001 - no secrets configured (local run): use .env / defaults
    pass

from travel_planner import world  # noqa: E402
from travel_planner.estimates import typical_budget  # noqa: E402
from travel_planner.llm import describe_llm  # noqa: E402
from travel_planner.memory.store import get_store  # noqa: E402
from travel_planner.options import DEFAULT_COUNTRY, INTERESTS, PACES, STYLES  # noqa: E402
from travel_planner.runner import TravelPlanner  # noqa: E402

st.set_page_config(page_title="Multi-Agent Travel Planner", page_icon="🧭", layout="wide")

TYPE_ICON = {"activity": "📍", "meal": "🍽️", "transport": "🚆", "lodging": "🛏️", "transit": "🚇", "free": "☕"}
AGENT_ICON = {"supervisor": "🧭", "research": "🔎", "weather": "🌦️", "budget": "💰", "booking": "🏨",
              "memory": "🧠", "validator": "✅", "user": "🙋"}
KIND_LABEL = {"task": "delegates", "report": "reports", "request": "asks", "reply": "replies", "notice": "informs",
              "ack": "acknowledges"}


@st.cache_resource
def get_planner() -> TravelPlanner:
    return TravelPlanner()   # keeps the LangGraph checkpointer alive across reruns


@st.cache_data
def country_list(continent: str | None) -> list[dict]:
    return world.countries(None if continent in (None, "All continents") else continent)


@st.cache_data
def region_list(iso: str) -> list[str]:
    return world.regions(iso)


@st.cache_data
def city_list(iso: str, region: str | None) -> list[dict]:
    return world.cities(iso, None if region in (None, "All regions") else region, limit=400)


def city_label(c: dict) -> str:
    pop = c["population"]
    size = f"{pop / 1e6:.1f}M" if pop >= 1e6 else f"{pop // 1000}k"
    return f"{c['name']}  ·  {c['region']}  ·  {size}"


planner = get_planner()
store = get_store()
ss = st.session_state
ss.setdefault("result", None)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    user_id = st.text_input("Traveller ID (for memory)", value="demo-user")
    st.caption(f"Model: **{describe_llm()}**")
    st.subheader("Failure injection (testing)")
    fail = {}
    for tool in ("weather", "places", "booking", "transport"):
        mode = st.selectbox(tool.title() + " API", ["normal", "fails once", "down"], key=f"f_{tool}")
        if mode != "normal":
            fail[tool] = 1 if mode == "fails once" else 99
    auto = st.toggle("Answer planner questions automatically", value=False)

    st.subheader("Preference memory")
    prefs = store.get_preferences(user_id)
    st.write("👍 " + (", ".join(prefs["likes"]) or "—"))
    st.write("👎 " + (", ".join(prefs["dislikes"]) or "—"))
    if prefs.get("notes"):
        st.write("📝 " + ", ".join(prefs["notes"]))
    if st.button("Forget my preferences"):
        store.forget(user_id)
        st.rerun()
    trips = store.past_trips(user_id)
    if trips:
        st.caption("Past trips: " + "; ".join(t["destination"] for t in trips))

# ---------------------------------------------------------------- input
st.title("🧭 Budget-Aware Multi-Agent Travel Planner")
st.caption("A supervisor and five specialist agents (Research, Weather, Budget, Booking, Memory) plan your trip "
           "together and talk to each other directly to resolve conflicts.")

tab_menu, tab_text = st.tabs(["📋 Plan with menus", "✍️ Describe in your own words"])

with tab_menu:
    st.markdown("##### Where are you going?")
    c1, c2, c3, c4 = st.columns([1, 1.3, 1.3, 1.6])
    continent = c1.selectbox("Continent", ["All continents"] + world.continents())
    countries = country_list(continent)
    names = [c["name"] for c in countries]
    default_idx = next((i for i, c in enumerate(countries) if c["iso"] == DEFAULT_COUNTRY), 0)
    country_name = c2.selectbox("Country", names, index=default_idx)
    country = countries[names.index(country_name)]
    region = c3.selectbox("Region / state / province", ["All regions"] + region_list(country["iso"]))
    cities = city_list(country["iso"], region)
    city = c4.selectbox("City", cities, format_func=city_label) if cities else None

    st.markdown("##### Travelling from (optional - adds transport to the plan)")
    o1, o2, o3 = st.columns([1.3, 1.3, 1.6])
    all_countries = country_list(None)
    o_names = ["— not needed —"] + [c["name"] for c in all_countries]
    origin_country_name = o1.selectbox("Home country", o_names)
    origin_country = next((c for c in all_countries if c["name"] == origin_country_name), None)
    origin_city = None
    if origin_country:
        o_region = o2.selectbox("Home region", ["All regions"] + region_list(origin_country["iso"]))
        o_cities = city_list(origin_country["iso"], o_region)
        origin_city = o3.selectbox("Home city", o_cities, format_func=city_label) if o_cities else None

    st.markdown("##### When, who and how much?")
    d1, d2, d3 = st.columns(3)
    start = d1.date_input("Start date", value=dt.date.today() + dt.timedelta(days=7), min_value=dt.date.today())
    days = d2.slider("Number of days", 1, 14, 3)
    travelers = d3.number_input("Travellers", min_value=1, max_value=12, value=1)

    currencies = world.currencies()
    default_cur = (world.country(origin_country["iso"])["currency"] if origin_country else country["currency"])
    default_cur = default_cur if default_cur in currencies else "USD"
    b1, b2, b3 = st.columns([1, 1, 2])
    currency = b2.selectbox("Currency", currencies, index=currencies.index(default_cur))
    style_now = ss.get("style_choice", "standard")
    est = typical_budget(city, origin_city, days, travelers, style_now) if city else {"total": 0, "transport": 0}
    typical = est["total"]
    raw = world.from_base(typical, currency)
    suggestion = round(raw, -1 if raw < 5000 else -3) or 1.0
    budget = b1.number_input(f"Total budget ({currency})", min_value=0.0, value=float(suggestion),
                             step=float(max(1, round(suggestion / 20, -1))),
                             key=f"budget_{currency}_{city and city['name']}_{origin_city and origin_city['name']}_"
                                 f"{days}_{travelers}_{style_now}")
    b3.caption(f"A typical {style_now} trip like this costs about **{world.money(typical, currency)}**"
               + (f", including about {world.money(est['transport'], currency)} return transport" if est["transport"]
                  else "")
               + f". Price level in {country['name']}: {world.price_index(country['iso']):.2f}× Korea.")

    st.markdown("##### What do you enjoy?")
    i1, i2 = st.columns(2)
    interests = i1.multiselect("Interests", list(INTERESTS), format_func=INTERESTS.get, default=["food", "culture"])
    avoid = i2.multiselect("Avoid", [k for k in INTERESTS if k not in interests], format_func=INTERESTS.get)
    s1, s2 = st.columns(2)
    style = s1.radio("Travel style", list(STYLES), index=1, format_func=STYLES.get, horizontal=False,
                     key="style_choice")
    pace = s2.radio("Pace", list(PACES), index=1, format_func=PACES.get, horizontal=False)
    notes = st.text_input("Anything else? (optional)", placeholder="e.g. vegetarian, travelling with a toddler, "
                                                                     "love street markets")

    if st.button("Plan my trip", type="primary", key="plan_form", disabled=city is None):
        form = {"country": country["iso"], "region": city["region"], "city": city["name"],
                "origin_country": origin_country["iso"] if origin_country else None,
                "origin_city": origin_city["name"] if origin_city else None,
                "start_date": start.isoformat(), "days": int(days), "travelers": int(travelers),
                "budget": float(budget), "currency": currency, "interests": interests, "avoid": avoid,
                "style": style, "pace": pace, "notes": notes}
        with st.spinner("Agents are planning and talking to each other..."):
            ss.result = planner.start(user_id=user_id, auto=auto, inject_failures=fail, form=form)
            ss.rated = False

with tab_text:
    request = st.text_area("Your trip request", height=90,
                           placeholder="e.g. 4 days in Lisbon from Madrid for 2 people, 1200 EUR, we love food and "
                                       "views but no nightlife")
    if st.button("Plan my trip", type="primary", key="plan_text", disabled=not request.strip()):
        with st.spinner("Agents are planning and talking to each other..."):
            ss.result = planner.start(request, user_id=user_id, auto=auto, inject_failures=fail)
            ss.rated = False

res = ss.result
st.divider()

# ---------------------------------------------------------------- human in the loop
if res is not None and res.status == "needs_input":
    q = res.question
    st.warning("**The plan needs your decision.**  \n" + "  \n".join(f"• {i}" for i in q["issues"]))
    labels = [o["label"] + (f"  — saves {o['saves_text']}" if o.get("saves_text") else "") for o in q["options"]]
    pick = st.radio("Which option should the agents apply?", range(len(labels)), format_func=lambda i: labels[i])
    if st.button("Apply and replan", type="primary"):
        with st.spinner("Re-running only the affected agents..."):
            ss.result = planner.resume(res.thread_id, q["options"][pick]["id"])
        st.rerun()


def conversation(messages: list[dict], peer_only: bool) -> None:
    for m in messages:
        if peer_only and m["kind"] not in ("request", "reply", "notice", "ack"):
            continue
        with st.chat_message(m["sender"], avatar=AGENT_ICON.get(m["sender"], "🤖")):
            arrow = f"**{m['sender'].title()}** {KIND_LABEL.get(m['kind'], m['kind'])} → **{m['receiver'].title()}**"
            badge = {"failed": " · ❌", "degraded": " · ⚠️"}.get(m["status"], "")
            st.markdown(f"{arrow}  ·  `{m['task']}`{badge}  \n{m.get('text') or ''}")
            if m.get("error"):
                st.caption(f"error: {m['error']}")


def talk_graph(messages: list[dict]) -> str:
    edges: dict[tuple[str, str], int] = {}
    for m in messages:
        if m["sender"] != m["receiver"]:
            edges[(m["sender"], m["receiver"])] = edges.get((m["sender"], m["receiver"]), 0) + 1
    peer = {(m["sender"], m["receiver"]) for m in messages if m["kind"] in ("request", "reply", "notice", "ack")}
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", '
             'fillcolor="#eef2ff", fontname="Helvetica", fontsize=16, margin="0.25,0.12"]; edge [fontname="Helvetica", fontsize=13, penwidth=1.6];']
    for (a, b), n in edges.items():
        color = "#d9480f" if (a, b) in peer else "#5c7cfa"
        lines.append(f'"{a}" -> "{b}" [label="{n}", color="{color}", fontcolor="{color}"];')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------- results
if res is not None and res.status in ("done", "failed") and res.state.get("itinerary"):
    s = res.state
    trip, it, val = s["trip"], s["itinerary"], s["validation"]
    cur = trip["currency"]

    def fmt(base: float) -> str:
        return world.money(base, cur)

    st.subheader(f"{trip['days']} days in {trip['destination']}, {trip['country_name']}")
    if val["passed"]:
        st.success(s.get("summary", ""))
    else:
        st.error("The plan still has open issues. " + s.get("summary", ""))
    if trip.get("assumptions"):
        st.info("Assumptions: " + "; ".join(trip["assumptions"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total cost", fmt(val["total"]))
    c2.metric("Budget", fmt(val["budget"]))
    c3.metric("Remaining", fmt(val["remaining"]))
    peer_count = sum(1 for m in s["messages"] if m["kind"] in ("request", "reply", "notice", "ack"))
    c4.metric("Agent-to-agent messages", peer_count)

    tabs = st.tabs([f"Day {d['day']} · {d['weekday']}" for d in it["days"]] +
                   ["💰 Budget", "💬 Agent conversation", "🧾 Raw"])
    for tab, d in zip(tabs, it["days"]):
        with tab:
            w = d["weather"]
            st.markdown(f"**{d['date']}** — {'🌧️' if w['bad'] else '⛅'} {w['description']}"
                        + (f", {w['tmin']}–{w['tmax']}°C" if w.get("tmax") is not None else "")
                        + (" · *bad-weather plan: indoor activities*" if w["bad"] else ""))
            rows = [{"Time": i["time"], "": TYPE_ICON.get(i["type"], ""), "Plan": i["name"],
                     "Cost": fmt(i["cost"]) if i.get("cost") else "free",
                     "Running total": fmt(i.get("cumulative", 0)), "Notes": i.get("notes", "")}
                    for i in d["items"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption(f"Day total {fmt(d['day_total'])} · cumulative {fmt(d['cumulative'])}")

    with tabs[len(it["days"])]:
        cats = {k.replace("_", " "): round(world.from_base(v, cur), 2) for k, v in it["totals"]["by_category"].items()}
        st.bar_chart(pd.DataFrame({cur: cats}), horizontal=True)
        if it.get("hotel"):
            h = it["hotel"]
            st.write(f"🛏️ **{h['name']}** — {fmt(h['total'])}"
                     + (f" · {h['distance_km']} km from the main activities" if h.get("distance_km") is not None else ""))
        if it.get("transport"):
            st.write(f"🚆 **{it['transport']['name']}** — {fmt(it['transport']['total'])} return")
        for issue in val["issues"]:
            (st.warning if issue["severity"] != "info" else st.info)(issue["message"])
        if s.get("decisions"):
            st.write("**Your decisions:** " + "; ".join(x["label"] for x in s["decisions"]))

    with tabs[len(it["days"]) + 1]:
        left, right = st.columns([1.6, 1])
        with right:
            st.markdown("**Who talked to whom**")
            st.graphviz_chart(talk_graph(s["messages"]))
            st.caption("Blue: supervisor tasks and reports. Orange: direct agent-to-agent messages.")
        with left:
            peer_only = st.toggle("Show only agent-to-agent messages", value=False)
            conversation(s["messages"], peer_only)

    with tabs[len(it["days"]) + 2]:
        st.download_button("Download itinerary (JSON)", json.dumps(it, ensure_ascii=False, indent=2),
                           file_name="itinerary.json")
        st.json(it, expanded=False)

    st.divider()
    if not ss.get("rated"):
        rating = st.feedback("stars")
        if rating is not None:
            store.add_feedback(user_id, None, rating + 1)
            ss.rated = True
            st.toast("Thanks! Your rating is used in the evaluation report.")
    else:
        st.caption("Thanks for rating this plan.")
elif res is not None and res.status == "failed":
    st.error(res.state.get("summary", "Planning failed."))
