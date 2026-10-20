"""Mocked accommodation / transport / ticketing endpoints that work for any city in the world.

Live booking APIs need commercial keys, so (as stated in the proposal) these endpoints are
mocked. They behave like a real API: prices follow the destination's price level, change with
the date (weekend surcharge), and some rooms are sold out on some nights - deterministically,
so results are reproducible. Curated cities use their curated hotel list.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math

from .. import world
from ..data import catalog

# base prices in KRW at price index 1.0
GENERIC_HOTELS = [("hostel", "Central Hostel (private room)", 1, 38000), ("budget", "Budget Inn", 2, 65000),
                  ("mid", "City Hotel", 3, 115000), ("up", "Grand Hotel", 4, 240000)]
BUDGET_LODGING = [("dorm", "Hostel dorm bed", 0, 18000), ("capsule", "Capsule / pod hotel", 0, 26000)]


def _h(*parts: str) -> int:
    return int(hashlib.md5("|".join(parts).encode()).hexdigest(), 16)


def _scale(trip: dict) -> float:
    big = 1.15 if (trip.get("population") or 0) > 1_000_000 else 1.0
    return trip.get("price_index", 1.0) * big


def _priced(trip: dict, hid: str, name: str, tier: int, base: float, start: dt.date, nights: int,
            travelers: int, lat, lon, per_bed: bool = False) -> dict:
    units = travelers if per_bed else max(1, math.ceil(travelers / 2))
    nightly, sold_out = [], False
    for n in range(nights):
        day = start + dt.timedelta(days=n)
        price = base * (1.2 if day.weekday() in (4, 5) else 1.0)     # Fri/Sat nights cost more
        nightly.append(int(round(price, -2)) * units)
        if _h(hid, trip["destination"], day.isoformat()) % 100 < 12:  # ~12% chance a night is sold out
            sold_out = True
    return {"id": hid, "name": name, "tier": tier, "rooms": units, "per_bed": per_bed, "nightly": nightly,
            "total": int(sum(nightly)), "available": not sold_out, "lat": lat, "lon": lon,
            "source": "mock booking API"}


def search_lodging(trip: dict, start: dt.date, nights: int, travelers: int) -> list[dict]:
    key = trip.get("catalog_key")
    if key:
        hotels = [(h[0], h[1], h[2], h[3], h[4], h[5]) for h in catalog.CITIES[key]["hotels"]]
    else:
        lat, lon, s = trip.get("lat"), trip.get("lon"), _scale(trip)
        hotels = []
        for i, (hid, name, tier, base) in enumerate(GENERIC_HOTELS):
            # spread the generic hotels a little around the centre so location matters
            dlat = ((_h(hid, trip["destination"]) % 100) - 50) / 2500 if lat is not None else 0
            dlon = ((_h(trip["destination"], hid) % 100) - 50) / 2500 if lon is not None else 0
            hotels.append((f"gen_{hid}", f"{trip['destination']} {name}", tier, base * s,
                           lat + dlat if lat is not None else None, lon + dlon if lon is not None else None))
    return [_priced(trip, hid, name, tier, base, start, nights, travelers, lat, lon)
            for hid, name, tier, base, lat, lon in hotels]


def search_budget_lodging(trip: dict, start: dt.date, nights: int, travelers: int) -> list[dict]:
    """Extra low-cost options (dorm beds, capsule hotels) - used when the Budget agent asks for cheaper rooms."""
    s = _scale(trip)
    return [_priced(trip, f"low_{hid}", f"{trip['destination']} {name}", tier, base * s, start, nights, travelers,
                    trip.get("lat"), trip.get("lon"), per_bed=True)
            for hid, name, tier, base in BUDGET_LODGING]


def search_transport(trip: dict) -> list[dict]:
    """Round-trip options between the origin and the destination (empty without an origin)."""
    if not trip.get("origin") or trip.get("origin_lat") is None:
        return []
    travelers = trip["travelers"]
    o_key, d_key = catalog.find_city(trip["origin"]), trip.get("catalog_key")
    curated = catalog.INTERCITY.get((o_key, d_key)) or catalog.INTERCITY.get((d_key, o_key)) if o_key and d_key else None
    if curated:
        options = curated
    else:
        km = world.distance_km({"lat": trip["origin_lat"], "lon": trip["origin_lon"]},
                               {"lat": trip["lat"], "lon": trip["lon"]})
        if km < 20:
            return []
        idx = (trip.get("price_index", 1.0) + trip.get("origin_price_index", 1.0)) / 2
        international = trip.get("origin_country") != trip.get("country")
        options = []
        if km <= 900 and not international or km <= 600:
            options.append(("train", "Train", int((6000 + 130 * km) * idx), round(0.4 + km / 160, 1)))
            options.append(("bus", "Coach / bus", int((4000 + 70 * km) * idx), round(0.5 + km / 70, 1)))
        if km >= 300:
            fare = (70000 + 95 * km) if international else (55000 + 110 * km)
            options.append(("flight", "Flight (economy)" if international else "Domestic flight",
                            int(fare * (0.6 + 0.4 * idx)), round(2.5 + km / 750, 1)))
            if km >= 1500:
                options.append(("flight", "Low-cost / connecting flight", int(fare * 0.7 * (0.6 + 0.4 * idx)),
                                round(4.5 + km / 650, 1)))
        if not options:
            options.append(("car", "Car hire / taxi", int((30000 + 400 * km) * idx), round(km / 60, 1)))
    return [{"id": f"tr_{i}", "mode": m, "name": n, "per_person_one_way": c, "hours": h,
             "total": int(c * 2 * travelers), "source": "mock transport API"}
            for i, (m, n, c, h) in enumerate(options)]


def local_transit(trip: dict, days: int, travelers: int) -> int:
    key = trip.get("catalog_key")
    per_day = catalog.CITIES[key]["transit_per_day"] if key else catalog.GENERIC_COSTS["transit_per_day"] * _scale(trip)
    return int(round(per_day, -2) * days * travelers)


def breakfast(trip: dict, style: str) -> int:
    key = trip.get("catalog_key")
    table = catalog.CITIES[key]["breakfast"] if key else {
        "normal": catalog.GENERIC_COSTS["breakfast_normal"] * _scale(trip),
        "budget": catalog.GENERIC_COSTS["breakfast_budget"] * _scale(trip)}
    return int(round(table["budget" if style == "budget" else "normal"], -2))


def quote_ticket(place: dict, date: dt.date, travelers: int) -> int:
    """Ticket price for an attraction on a given date (weekend +10% for paid sights)."""
    base = place.get("cost", 0) or 0
    if base and date.weekday() >= 5:
        base = round(base * 1.1, -2)
    return int(base * travelers)
