"""Memory agent: stores and retrieves long-term preferences across sessions (retrieve · apply · update)."""
from __future__ import annotations

from ..memory.store import get_store
from ..state import PlanState, message


def memory_retrieve(state: PlanState) -> dict:
    user = state.get("user_id", "default")
    store = get_store()
    prefs = store.get_preferences(user)
    past = store.past_trips(user, limit=3)
    result = {"likes": prefs["likes"], "dislikes": prefs["dislikes"], "past_trips": len(past)}
    return {
        "preferences": {"likes": prefs["likes"], "dislikes": prefs["dislikes"], "notes": prefs.get("notes", []),
                        "from_memory": True},
        "messages": [message("memory", "supervisor", "retrieve_preferences", {"user_id": user}, result,
                             text=("Remembered preferences - likes: " + (", ".join(prefs["likes"]) or "none")
                                   + "; dislikes: " + (", ".join(prefs["dislikes"]) or "none")
                                   + f"; {len(past)} past trip(s).") if prefs["likes"] or prefs["dislikes"] or past
                             else "No stored preferences yet for this traveller.")],
    }


def memory_update(state: PlanState) -> dict:
    """Persist what was learned in this session: stated preferences and choices the user made."""
    user = state.get("user_id", "default")
    store = get_store()
    trip = state.get("trip", {})
    learned = []
    for tag in trip.get("request_likes", []):
        store.upsert_preference(user, "likes", tag)
        learned.append(f"likes {tag}")
    for tag in trip.get("request_dislikes", []):
        store.upsert_preference(user, "dislikes", tag)
        learned.append(f"dislikes {tag}")
    for d in state.get("decisions", []):
        if d.get("type") == "budget_meals":
            store.upsert_preference(user, "notes", "accepts budget meals", 0.5)
            learned.append("accepts budget meals")
        if d.get("type") == "cheaper_hotel":
            store.upsert_preference(user, "notes", "price-sensitive on lodging", 0.5)
            learned.append("price-sensitive on lodging")
    val = state.get("validation", {})
    trip_id = None
    if state.get("itinerary"):
        trip_id = store.save_trip(user, state.get("request", ""), trip.get("destination", ""),
                                  int(val.get("total", 0)), int(trip.get("budget_krw", 0)), state["itinerary"])
    return {"messages": [message("memory", "supervisor", "update_preferences", {"user_id": user},
                                 {"learned": learned, "trip_id": trip_id},
                                 text="Saved for next time: " + (", ".join(learned) or "nothing new") + ".")]}
