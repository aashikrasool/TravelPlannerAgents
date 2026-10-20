"""Places tool - works for any city in the world.

* Curated cities (Busan, Seoul, Jeju, Gyeongju) are served from the offline catalog, with
  hand-checked tags and price estimates.
* Every other city is looked up live on OpenStreetMap (Overpass API, free, no key): museums,
  galleries, viewpoints, attractions, parks, beaches, markets, zoos and restaurants, tagged from
  their OSM tags and priced with the country's price level.
* `generic_places` is the last-resort fallback the user can accept when every source fails.
"""
from __future__ import annotations

import hashlib

import requests

from ..config import settings
from ..data import catalog

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# (osm key, value) -> (tags, indoor, base cost in KRW at price index 1.0, hours)
_OSM_TAGS = {
    ("tourism", "museum"): (["history", "culture"], True, 8000, 2.0),
    ("tourism", "gallery"): (["art", "culture"], True, 5000, 1.5),
    ("tourism", "aquarium"): (["family", "animals"], True, 25000, 2.0),
    ("tourism", "zoo"): (["family", "animals"], False, 15000, 2.5),
    ("tourism", "theme_park"): (["family", "theme_park"], False, 45000, 4.0),
    ("tourism", "viewpoint"): (["view", "nature"], False, 0, 1.0),
    ("tourism", "attraction"): (["culture", "city"], False, 0, 1.5),
    ("historic", "castle"): (["history", "culture", "architecture"], False, 8000, 1.5),
    ("historic", "monument"): (["history", "architecture"], False, 0, 0.5),
    ("historic", "memorial"): (["history"], False, 0, 0.5),
    ("amenity", "place_of_worship"): (["history", "culture", "architecture"], False, 0, 1.0),
    ("amenity", "marketplace"): (["food", "market", "shopping"], False, 0, 1.5),
    ("amenity", "theatre"): (["art", "culture", "evening", "nightlife"], True, 30000, 2.0),
    ("leisure", "park"): (["nature", "relax"], False, 0, 1.5),
    ("leisure", "garden"): (["nature", "relax"], False, 0, 1.0),
    ("natural", "beach"): (["beach", "nature"], False, 0, 2.0),
    ("natural", "peak"): (["nature", "hiking", "view"], False, 0, 3.0),
    ("shop", "mall"): (["shopping"], True, 0, 2.0),
}


def search_catalog(city_key: str) -> list[dict]:
    return catalog.attractions(city_key) + catalog.restaurants(city_key)


def search_osm(city: str, lat: float, lon: float, price_index: float = 1.0, radius_m: int = 9000) -> list[dict]:
    """Live lookup on OpenStreetMap around the city centre."""
    a, r = radius_m, radius_m // 2
    query = f"""
    [out:json][timeout:25];
    (
      nwr(around:{a},{lat},{lon})[tourism~"^(museum|gallery|aquarium|zoo|theme_park|viewpoint|attraction)$"][name];
      nwr(around:{a},{lat},{lon})[historic~"^(castle|monument)$"][name][wikidata];
      nwr(around:{a},{lat},{lon})[amenity=place_of_worship][name][wikidata];
      nwr(around:{a},{lat},{lon})[amenity~"^(marketplace|theatre)$"][name];
      nwr(around:{a},{lat},{lon})[leisure~"^(park|garden)$"][name][wikidata];
      nwr(around:{a},{lat},{lon})[natural~"^(beach|peak)$"][name];
      nwr(around:{a},{lat},{lon})[shop=mall][name];
    )->.sights;
    .sights out center 150;
    nwr(around:{r},{lat},{lon})[amenity=restaurant][name][cuisine]->.food;
    .food out center 60;
    """
    last_exc: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query}, timeout=settings.http_timeout * 4)
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            break
        except Exception as exc:  # noqa: BLE001 - try the mirror
            last_exc = exc
    else:
        raise RuntimeError(f"Overpass API unreachable: {last_exc}")

    places: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        plat = el.get("lat") or el.get("center", {}).get("lat")
        plon = el.get("lon") or el.get("center", {}).get("lon")
        if not name or plat is None or name.lower() in seen:
            continue
        seen.add(name.lower())
        famous = bool(tags.get("wikipedia") or tags.get("wikidata"))
        if tags.get("amenity") == "restaurant":
            cuisine = tags.get("cuisine", "local").split(";")[0]
            fancy = cuisine in ("french", "fine_dining", "steak_house", "sushi", "seafood")
            places.append({"id": f"osm_{el['id']}", "name": name, "kind": "restaurant",
                           "tags": ["food", cuisine] + (["seafood"] if cuisine in ("seafood", "sushi") else []),
                           "cost": int(round((26000 if fancy else 13000) * price_index, -2)), "meal": "any",
                           "indoor": True, "hours": 1.0, "lat": plat, "lon": plon, "area": city,
                           "popularity": 4 if famous else 3, "source": "OpenStreetMap"})
            continue
        for (k, v), (ptags, indoor, base, hours) in _OSM_TAGS.items():
            if tags.get(k) == v:
                fee = tags.get("fee")
                cost = 0 if fee == "no" else int(round(base * price_index, -2))
                places.append({"id": f"osm_{el['id']}", "name": name, "kind": "attraction", "tags": list(ptags),
                               "indoor": indoor, "cost": cost, "hours": hours, "lat": plat, "lon": plon,
                               "area": city, "popularity": 4 if famous else 3,
                               "description": tags.get("description", "")[:140], "source": "OpenStreetMap"})
                break
    if sum(1 for p in places if p["kind"] == "attraction") < 3:
        raise ValueError(f"OpenStreetMap returned too few places for {city}")
    return places


def _jitter(seed: str, lat, lon):
    if lat is None:
        return None, None
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return lat + ((h % 200) - 100) / 4000, lon + (((h // 200) % 200) - 100) / 4000


def generic_places(city: str, lat=None, lon=None, price_index: float = 1.0) -> list[dict]:
    """Placeholder plan items, used only when the user accepts a generic plan."""
    g = catalog.GENERIC_COSTS
    items = [
        ("gen_walk", "Old-town walking tour", ["culture", "city", "architecture"], False, 0),
        ("gen_museum", "City history museum", ["history", "culture"], True, g["museum"]),
        ("gen_market", "Central market food tour", ["food", "market"], True, 0),
        ("gen_park", "Main city park", ["nature", "relax"], False, 0),
        ("gen_gallery", "Local art gallery", ["art"], True, g["museum"]),
        ("gen_view", "City viewpoint at sunset", ["view", "evening"], False, 0),
        ("gen_shopping", "Shopping district", ["shopping"], True, 0),
        ("gen_cafe", "Cafe street", ["food", "relax"], True, 0),
        ("gen_waterfront", "Waterfront or riverside walk", ["nature", "view"], False, 0),
        ("gen_temple", "Historic church, temple or mosque", ["history", "culture", "architecture"], False, 0),
        ("gen_library", "Public library or cultural centre", ["culture"], True, 0),
        ("gen_science", "Science or natural history museum", ["family", "history"], True, g["museum"]),
        ("gen_night_market", "Night market", ["food", "market", "evening"], False, 0),
    ]
    out = []
    for i, n, t, ind, c in items:
        plat, plon = _jitter(i + city, lat, lon)
        out.append({"id": i, "name": f"{n} ({city})", "kind": "attraction", "tags": t, "indoor": ind,
                    "cost": int(round(c * price_index, -2)), "hours": 1.5, "lat": plat, "lon": plon, "area": city,
                    "popularity": 3, "source": "generic"})
    for rid, n, meal, cost in [("gen_lunch", "Local lunch spot", "lunch", g["restaurant_lunch"]),
                               ("gen_dinner", "Local dinner restaurant", "dinner", g["restaurant_dinner"]),
                               ("gen_street", "Street food stalls", "any", 7000),
                               ("gen_eatery", "Budget local eatery", "any", 10000)]:
        plat, plon = _jitter(rid + city, lat, lon)
        out.append({"id": rid, "name": f"{n} ({city})", "kind": "restaurant", "tags": ["food"],
                    "cost": int(round(cost * price_index, -2)), "meal": meal, "indoor": True, "hours": 1.0,
                    "lat": plat, "lon": plon, "area": city, "popularity": 2 if cost < 11000 else 3,
                    "source": "generic"})
    return out


def extra_indoor_places(city: str, lat=None, lon=None, price_index: float = 1.0, exclude: set | None = None) -> list[dict]:
    """Indoor options the Research agent adds when the Weather agent asks for more rainy-day activities."""
    exclude = exclude or set()
    return [p for p in generic_places(city, lat, lon, price_index)
            if p["kind"] == "attraction" and p["indoor"] and p["id"] not in exclude]
