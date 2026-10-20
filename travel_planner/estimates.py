"""Quick budget estimate shown next to the budget field in the app and CLI."""
from __future__ import annotations

from . import world
from .tools import booking

PER_DAY = {"budget": 75000, "standard": 110000, "comfort": 170000}   # KRW per person per day at price index 1.0


def typical_budget(dest: dict, origin: dict | None, days: int, travelers: int, style: str = "standard") -> dict:
    """Return {'total', 'stay', 'transport'} in the base currency for a typical trip."""
    stay = PER_DAY.get(style, PER_DAY["standard"]) * world.price_index(dest["country"]) * days * travelers
    transport = 0
    if origin:
        trip = {"destination": dest["name"], "country": dest["country"], "lat": dest["lat"], "lon": dest["lon"],
                "price_index": world.price_index(dest["country"]), "origin": origin["name"],
                "origin_country": origin["country"], "origin_lat": origin["lat"], "origin_lon": origin["lon"],
                "origin_price_index": world.price_index(origin["country"]), "travelers": travelers,
                "catalog_key": None}
        options = booking.search_transport(trip)
        transport = min((o["total"] for o in options), default=0)
    return {"total": int(stay + transport), "stay": int(stay), "transport": int(transport)}
