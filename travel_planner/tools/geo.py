"""Distance and travel-time helpers."""
from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(a: dict | None, b: dict | None) -> int:
    """Rough door-to-door time by public transport / taxi inside a city."""
    if not a or not b or a.get("lat") is None or b.get("lat") is None:
        return 20
    km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    if km < 0.8:
        return 10                       # walking distance
    # waiting time + ~22 km/h for city legs, ~40 km/h for the part of a trip beyond 5 km
    return int(round(12 + min(km, 5) / 22 * 60 + max(0.0, km - 5) / 40 * 60))


def order_by_proximity(items: list[dict], start: dict | None) -> list[dict]:
    """Greedy nearest-neighbour ordering to keep each day's route short."""
    remaining = list(items)
    ordered: list[dict] = []
    cur = start
    while remaining:
        nxt = min(remaining, key=lambda x: travel_minutes(cur, x))
        ordered.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    return ordered
