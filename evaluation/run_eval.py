"""Run every test scenario through the multi-agent system and the single-agent baseline.

    python -m evaluation.run_eval                 # offline + reproducible (mock weather)
    python -m evaluation.run_eval --live          # use live APIs where available

Writes evaluation/results/<timestamp>/results.csv and report.md.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def metrics(trip: dict, state: dict | None, validation: dict | None, status: str, decisions: list) -> dict:
    if status != "done" or not state or not validation:
        return {"produced_plan": 0, "budget_compliant": 0, "completeness": 0.0, "feasibility": 0.0,
                "preference_violations": 0, "preference_match": 0.0, "total_krw": None,
                "budget_krw": trip.get("budget_krw") if trip else None, "decisions": len(decisions)}
    comp = validation["completeness"]
    n = max(1, comp["days"])
    completeness = (comp["with_activity"] + comp["with_lunch"] + comp["with_dinner"]) / (3 * n)
    if not comp["lodging_ok"]:
        completeness *= 0.75
    prefs = state.get("preferences", {})
    likes, dislikes = set(prefs.get("likes", [])), set(prefs.get("dislikes", []))
    acts = [i for d in state["itinerary"]["days"] for i in d["items"] if i["type"] == "activity"]
    tags_of = {c["id"]: set(c.get("tags", [])) for c in state.get("candidates", [])}
    violations = sum(1 for a in acts if tags_of.get(a.get("place_id"), set()) & dislikes)
    match = (sum(1 for a in acts if tags_of.get(a.get("place_id"), set()) & likes) / len(acts)) if acts and likes else 1.0
    accepted_over = any(d.get("type") == "accept_over_budget" for d in decisions)
    total = validation["total"]
    return {"produced_plan": 1,
            "budget_compliant": int(total <= validation["budget"] and not accepted_over),
            "completeness": round(completeness, 3),
            "feasibility": round(validation["feasible_days"] / max(1, validation["n_days"]), 3),
            "preference_violations": violations, "preference_match": round(match, 3),
            "total_krw": total, "budget_krw": validation["budget"], "decisions": len(decisions)}


def run(live: bool) -> Path:
    if not live:
        os.environ.setdefault("WEATHER_MODE", "mock")
        os.environ.setdefault("PLACES_MODE", "mock")
        os.environ.setdefault("FX_MODE", "fixed")
    out_dir = HERE / "results" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MEMORY_DB"] = str(out_dir / "eval_memory.sqlite")   # fresh memory, no leakage between runs

    from travel_planner.baseline import run_baseline
    from travel_planner.llm import describe_llm
    from travel_planner.runner import TravelPlanner

    scenarios = json.loads((HERE / "scenarios.json").read_text())
    planner = TravelPlanner()
    rows = []
    for sc in scenarios:
        user = f"eval_{sc['id']}"
        t0 = time.time()
        res = planner.start(sc.get("request", ""), user_id=user, auto=True, inject_failures=dict(sc["failures"]),
                            form=sc.get("form"))
        st = res.state
        m = metrics(st.get("trip", {}), st, st.get("validation"), res.status, st.get("decisions", []))
        where = f"{st['trip']['destination']}, {st['trip']['country']}" if st.get("trip") else "?"
        peer = sum(1 for x in st.get("messages", []) if x["kind"] in ("request", "reply", "notice", "ack"))
        rows.append({"scenario": sc["id"], "destination": where, "system": "multi-agent",
                     "failures": json.dumps(sc["failures"]), "runtime_s": round(time.time() - t0, 2),
                     "replans": st.get("iterations", 0), "peer_messages": peer, **m})

        b = run_baseline(sc.get("request", ""), user_id=user + "_b", inject_failures=dict(sc["failures"]),
                         form=sc.get("form"))
        mb = metrics(b.get("trip", {}), b.get("state"), b.get("validation"), b["status"], [])
        rows.append({"scenario": sc["id"], "destination": where, "system": "single-agent baseline",
                     "failures": json.dumps(sc["failures"]), "runtime_s": round(b["runtime"], 2), "replans": 0,
                     "peer_messages": 0, **mb})
        print(f"{sc['id']}: multi={m['produced_plan']}/{m['budget_compliant']} "
              f"baseline={mb['produced_plan']}/{mb['budget_compliant']}")

    with open(out_dir / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def agg(system: str, only_failures: bool | None = None) -> dict:
        rs = [r for r in rows if r["system"] == system and
              (only_failures is None or (r["failures"] != "{}") == only_failures)]
        return {"n": len(rs),
                "plan_rate": statistics.mean(r["produced_plan"] for r in rs),
                "budget_compliance": statistics.mean(r["budget_compliant"] for r in rs),
                "completeness": statistics.mean(r["completeness"] for r in rs),
                "feasibility": statistics.mean(r["feasibility"] for r in rs),
                "pref_match": statistics.mean(r["preference_match"] for r in rs),
                "violations": sum(r["preference_violations"] for r in rs),
                "runtime": statistics.mean(r["runtime_s"] for r in rs)}

    systems = ["multi-agent", "single-agent baseline"]
    lines = [f"# Evaluation report - {dt.datetime.now():%Y-%m-%d %H:%M}", "",
             f"LLM: {describe_llm()} | weather/places: {'live' if live else 'offline (cities outside the curated catalog use generic placeholders)'} | "
             f"scenarios: {len(scenarios)}", "",
             "## Overall", "",
             "| System | Plans produced | Budget compliance | Completeness | Schedule feasibility | Preference match | Preference violations | Avg runtime (s) |",
             "|---|---|---|---|---|---|---|---|"]
    for s in systems:
        a = agg(s)
        lines.append(f"| {s} | {a['plan_rate']:.0%} | {a['budget_compliance']:.0%} | {a['completeness']:.0%} | "
                     f"{a['feasibility']:.0%} | {a['pref_match']:.0%} | {a['violations']} | {a['runtime']:.2f} |")
    lines += ["", "## Failure recovery (scenarios with injected API failures)", "",
              "Recovery = a complete, budget-compliant plan was still delivered.", "",
              "| System | Scenarios | Recovery rate |", "|---|---|---|"]
    for s in systems:
        rs = [r for r in rows if r["system"] == s and r["failures"] != "{}"]
        rec = statistics.mean(int(r["produced_plan"] and r["budget_compliant"] and r["completeness"] >= 0.99)
                              for r in rs)
        lines.append(f"| {s} | {len(rs)} | {rec:.0%} |")
    lines += ["", "## Per scenario", "",
              "| Scenario | Destination | Failures | System | Plan | Budget OK | Total / budget (KRW) | User decisions | Agent-to-agent msgs |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        tb = f"{r['total_krw']:,} / {r['budget_krw']:,}" if r["total_krw"] is not None else "-"
        lines.append(f"| {r['scenario']} | {r['destination']} | {r['failures']} | {r['system']} | "
                     f"{'yes' if r['produced_plan'] else 'no'} | {'yes' if r['budget_compliant'] else 'no'} | {tb} | "
                     f"{r['decisions']} | {r['peer_messages']} |")

    from travel_planner.memory.store import MemoryStore
    ratings = MemoryStore(str(HERE.parent / "data" / "memory.sqlite")).ratings() \
        if (HERE.parent / "data" / "memory.sqlite").exists() else []
    lines += ["", "## User assessment", ""]
    lines.append(f"Mean user rating: {statistics.mean(ratings):.2f} / 5 from {len(ratings)} ratings collected in the app."
                 if ratings else "No user ratings collected yet - rate plans in the Streamlit app (1-5 stars).")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nSaved to {out_dir}")
    return out_dir


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use live weather/places APIs")
    run(ap.parse_args().live)
