# Budget-Aware Multi-Agent Travel Itinerary Planner

Modern Artificial Intelligence · 08849_001 — Aashik Rasool, Karimi Abdul Hai, Latino Francesca Pia

A team of AI agents turns a trip request into a validated, budget-compliant, weather-aware, day-by-day itinerary for **any city in the world**. The plan reflects the traveller's preferences and remembers them across sessions.

You can ask in two ways:

* **Dropdown menus:** Continent → Country → Region / state → City, home city, dates, travellers, budget and currency, interests, things to avoid, travel style and pace.
* **Your own words**, for example *"4 days in Lisbon from Madrid for 2 people, 1200 EUR, we love food and views but no nightlife."*

Everything is open source and free to run. LangGraph runs the agents and Ollama runs an open model (Qwen2.5, Llama 3.1, Mistral…). Weather comes from Open-Meteo and places from OpenStreetMap, with country and city data from GeoNames. Memory is stored in SQLite, and the interfaces are Streamlit and a menu-driven CLI. No API keys are needed.

---

## 1. Architecture

```
                 ┌──────────────────────────── shared PlanState ─────────────────────────────┐
                 │ trip · preferences · candidates · forecast · lodging · prices · itinerary │
                 │ messages (full log) · mailbox (agent-to-agent messages to deliver)        │
                 └───────────────────────────────────────────────────────────────────────────┘
 user ─► Memory ─► SUPERVISOR ─┬─► Research ─► Weather ─┐
                    (delegates) │                        ├─► Budget ─► MESSAGE ROUTER ─► SUPERVISOR ─► Validation
                               └─► Booking ─────────────┘           (agents talk        (merge)       & budget check
                                                                     to each other)                       │
                         SUPERVISOR (final answer) ─► Memory (update) ◄──────── pass ◄────────────────────┤
                                                                                                    fail  ▼
                                      MESSAGE ROUTER ◄── targeted replan ◄── Human-in-the-loop: user decides
```

| Agent | File | Role |
|---|---|---|
| **Supervisor** | `agents/supervisor.py` | Reads the form or the free text (LLM + rule-based fallback), resolves the city anywhere in the world, delegates tasks, merges results, writes the final answer |
| **Research** | `agents/research.py` | Finds attractions and restaurants (curated catalog or live OpenStreetMap), scores them against likes and dislikes, and asks the LLM to rate fit. Ratings for place ids that don't exist are thrown away. |
| **Weather** | `agents/weather_agent.py` | Gets the forecast and proposes indoor alternatives for every outdoor activity on a bad-weather day |
| **Budget** | `agents/budget.py` | Converts currencies, scales prices to the country's price level, allocates the budget by travel style, picks the hotel and transport |
| **Booking** | `agents/booking_agent.py` | Gathers priced accommodation (with availability) and transport between any two cities |
| **Memory** | `agents/memory_agent.py` | Retrieves preferences at the start and stores new ones at the end |
| Validator | `agents/validator.py` | Checks budget compliance, completeness and schedule feasibility; builds the user's options; runs targeted replanning |

### How the agents communicate

**1. Supervisor ↔ specialists.** The supervisor delegates a task to each specialist, and each specialist reports back. Research and Booking run in parallel, and Budget waits for both.

**2. Agents talk to each other directly** (`travel_planner/bus.py`). Any agent can send a **request** or a **notice** to any other agent. The graph's message router delivers each message to the receiver's handler. The receiver acts on it and **replies**, and the reply can start the next step of the conversation. The supervisor merges the plan only when every conversation is finished.

| Conversation | What happens |
|---|---|
| Research → Booking `prefer_area` | "Most matching activities are around Haeundae, rank hotels by distance." Booking re-ranks and replies, then tells Budget `lodging_reranked`, and Budget re-checks its hotel choice. |
| Weather → Research `need_indoor_activities` | "Rain on days 2–3 and only 2 indoor options for 6 slots." Research adds indoor places and replies. Weather rebuilds its swap table and tells Budget `new_candidates` so the new places get priced. |
| Budget → Booking `find_cheaper_lodging` | "Cheapest room is above my lodging target." Booking searches dorm and capsule options and replies, and Budget switches hotel. |
| Budget → Research `flag_expensive` | "Activity budget is about X per person." Research down-ranks pricey sights and replies with the free options available. |

Every message has the same structure: `sender · receiver · kind (task/report/request/reply/notice/ack) · task · text · input · result · status · error · reply_to`. The app's **💬 Agent conversation** tab shows them as a chat, with a "who talked to whom" graph. The CLI prints the agent-to-agent part after every plan.

### Failure handling and human-in-the-loop

1. A tool fails (each tool is retried once) or the draft exceeds the budget.
2. **Graceful degradation**:
   - Weather falls back to a seasonal estimate based on latitude and season.
   - Booking falls back to list prices.
   - Transport falls back to distance-based fares.
3. **User choice**: when the plan can't be fixed silently, the graph pauses with LangGraph's `interrupt` and asks, for example: drop an activity / choose cheaper meals / switch hotel / keep it over budget.
4. **Targeted replanning**: only the affected agents re-run, and their new messages go through the router again.
5. **Final check**: the revised plan is validated again.

## 2. Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell)      |  source .venv/bin/activate   on Mac/Linux
pip install -r requirements.txt

# recommended: an open-source LLM through Ollama (https://ollama.com/download)
ollama pull qwen2.5:7b            # or llama3.1:8b, mistral:7b, qwen2.5:3b for lighter laptops

copy .env.example .env            # Windows   |   cp .env.example .env   on Mac/Linux   (optional)
```

Without Ollama, the planner still works end to end: every agent has a rule-based fallback, and the app shows which mode is active. To use another open-model server (vLLM, LM Studio, llama.cpp, Groq…), set `LLM_PROVIDER=openai`, `OPENAI_BASE_URL` and `LLM_MODEL` in `.env`.

## 3. Run it

| What | Command |
|---|---|
| **Web app** (dropdown menus + chat view of the agents) | `streamlit run app.py` |
| **CLI with menus** (arrow keys, type to search countries and cities) | `python cli.py` |
| CLI with free text | `python cli.py "4 days in Lisbon from Madrid, 1200 EUR, food and views"` |
| Show every message | `python cli.py --show-messages "..."` |
| Simulate an API outage | `python cli.py --fail weather=99 --fail booking=99 "..."` (`99` = down, `1` = fails once) |
| Evaluation (multi-agent vs single-agent) | `python -m evaluation.run_eval` (offline) or `python -m evaluation.run_eval --live` |
| Tests | `pytest -q` (28 tests) |

### Where the data comes from

| Data | Source | Coverage |
|---|---|---|
| Countries, regions, cities (dropdowns) | GeoNames, bundled in `data/world.json.gz` | 247 countries, 34,000 cities with population ≥ 15,000 |
| Places to visit and restaurants | OpenStreetMap Overpass API (live) | any city; needs internet |
| Curated places with checked prices | `data/catalog.py` | Busan, Seoul, Jeju, Gyeongju (works offline) |
| Weather | Open-Meteo (live, next ~2 weeks); seasonal estimate by latitude otherwise | any location |
| Prices | country price-level index × base prices (`world.py`) | every country (continent default if unlisted) |
| Exchange rates | open.er-api.com (live), fixed table as fallback | ~90 currencies |
| Hotels / transport | mocked booking APIs: prices follow price level and distance, with weekend surcharges and sell-outs | any pair of cities |

If OpenStreetMap can't be reached and the city isn't in the curated catalog, the planner asks whether to retry or continue with a generic plan of placeholder activities.

## 4. Deploy online (Streamlit Community Cloud + Groq, free)

The local Ollama model can't run on free hosting, so the online version uses the same kind of open-source model (Llama 3.1 8B) served by Groq through its OpenAI-compatible API.

1. **Upload to GitHub.** Create a repository and upload the *contents* of this folder, so that `app.py` sits at the top level of the repository. Never upload a `.env` or `.streamlit/secrets.toml` that contains your key.
2. **Create the app.** Go to <https://share.streamlit.io>, sign in with GitHub and choose **Create app**. Pick your repository and branch `main`, and set the main file to `app.py`.
3. **Add the key privately.** Open **Advanced settings → Secrets** and paste the lines from `.streamlit/secrets.toml.example`, with your real Groq key:
   ```toml
   LLM_PROVIDER = "openai"
   OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
   OPENAI_API_KEY = "gsk_..."
   LLM_MODEL = "llama-3.1-8b-instant"
   ```
4. **Deploy.** The sidebar should show `Model: llama-3.1-8b-instant (openai-compatible via api.groq.com)`. If it shows *rule-based*, the key or the model name is wrong. Fix it under **App settings → Secrets**, then reboot the app.

Notes: on the free tier, the memory database resets when the app restarts. Groq's free plan has rate limits, which is enough for demos. To use Groq on your own laptop, put the same four lines in a `.env` file, written as `KEY=value` without quotes or spaces.

## 5. Evaluation

`evaluation/scenarios.json` holds 18 scenarios:

- **Destinations:** Korea, Japan, France, Turkey and the USA.
- **Input:** both free-text requests and dropdown forms.
- **Variety:** different budgets, group sizes, currencies (KRW, EUR, PKR, USD) and preferences.
- **Failures:** 7 scenarios with injected API failures.

Both systems get **the same tools and data**. The single-agent baseline (`baseline.py`) calls the tools sequentially in one context. It has no agent-to-agent messages, no validation and replanning loop, and no fallback when a tool fails.

Offline run (rule-based mode, mock weather, no internet):

| System | Plans produced | Budget compliance | Completeness | Schedule feasibility | Recovery under API failures |
|---|---|---|---|---|---|
| Multi-agent | 100% | 100% | 100% | 97% | 100% (7/7) |
| Single-agent baseline | 50% | 39% | 50% | 47% | 29% (2/7) |

In the offline run, the cities outside the curated catalog (Tokyo, Paris, Istanbul, New York) have no live place data. The multi-agent system asks and then uses a generic plan; the baseline stops. Run `--live` with internet, and Ollama running, for the real figures. Each run writes `evaluation/results/<timestamp>/results.csv` and `report.md`, including the number of agent-to-agent messages per scenario.

## 6. Project structure

```
travel-planner-agents/
├── app.py                      Streamlit UI: dropdown form, human-in-the-loop, agent chat + graph, ratings
├── cli.py                      CLI: interactive menus (questionary) or free text
├── travel_planner/
│   ├── graph.py                LangGraph workflow (parallel branches, message router, interrupt, replan loop)
│   ├── bus.py                  agent-to-agent message bus: request / reply / notice handlers
│   ├── state.py                shared PlanState, message format, mailbox reducer
│   ├── world.py                countries / regions / cities, price levels, currencies
│   ├── estimates.py            "typical budget" hint for the forms
│   ├── options.py              dropdown choices (interests, style, pace)
│   ├── agents/                 supervisor, research, weather, budget, booking, memory, validator, scheduler
│   ├── tools/                  weather (Open-Meteo), places (OSM + catalog), booking (mock), failures
│   ├── memory/store.py         SQLite preference memory
│   └── data/                   world.json.gz (GeoNames), catalog.py (curated Korean cities)
├── scripts/build_world_data.py rebuilds world.json.gz from GeoNames
├── evaluation/                 scenarios + evaluation harness
└── tests/                      28 tests (agents, peer messaging, HITL, failures, memory, world data, LLM clients)
```

## 7. Notes and limitations

* Hotel and transport endpoints are **mocked**, as stated in the proposal. Prices are estimates: base prices × the country's price level, or curated prices for the Korean cities.
* Times are local. Long-haul trips arrive later on day 1, which becomes a settling-in day. Time-zone shifts are not modelled.
* The proposal named OpenWeatherMap and Google Places. We use Open-Meteo and OpenStreetMap instead, because they are open, free and need no key.
* Country, region and city data: © GeoNames, CC-BY 4.0. Map data: © OpenStreetMap contributors, ODbL.
