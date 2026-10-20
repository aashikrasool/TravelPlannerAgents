"""Builds travel_planner/data/world.json.gz (countries, regions, cities) from GeoNames data.

Only needed to refresh the bundled file. Sources (CC-BY 4.0, https://www.geonames.org):
  * geonamescache  (pip)  - countries + cities with population >= 15,000
  * rg_cities1000.csv     - from the reverse_geocoder sdist, gives each city's region (admin1) name
Usage: python scripts/build_world_data.py path/to/rg_cities1000.csv
"""
import csv
import gzip
import json
import sys
from pathlib import Path

import geonamescache

CONTINENTS = {"AF": "Africa", "AS": "Asia", "EU": "Europe", "NA": "North America", "OC": "Oceania",
              "SA": "South America", "AN": "Antarctica"}

g = geonamescache.GeonamesCache(min_city_population=15000)
grid: dict = {}
with open(sys.argv[1], encoding="utf-8") as f:
    for row in csv.DictReader(f):
        lat, lon = float(row["lat"]), float(row["lon"])
        if row["admin1"]:
            grid.setdefault((row["cc"], round(lat, 1), round(lon, 1)), []).append((lat, lon, row["admin1"]))


def region_of(cc: str, lat: float, lon: float) -> str:
    """Region (admin1) of the nearest GeoNames place in the same country, within ~15 km."""
    best, best_d = "", 0.15 ** 2
    for dlat in (-0.1, 0.0, 0.1):
        for dlon in (-0.1, 0.0, 0.1):
            for plat, plon, name in grid.get((cc, round(lat + dlat, 1), round(lon + dlon, 1)), []):
                d = (plat - lat) ** 2 + (plon - lon) ** 2
                if d < best_d:
                    best, best_d = name, d
    return best

countries = {}
for iso, c in g.get_countries().items():
    if c["continentcode"] == "AN":
        continue
    countries[iso] = {"name": c["name"], "continent": CONTINENTS[c["continentcode"]],
                      "currency": c["currencycode"] or "USD", "capital": c["capital"]}

cities = []
matched = 0
for c in g.get_cities().values():
    cc = c["countrycode"]
    if cc not in countries:
        continue
    region = region_of(cc, c["latitude"], c["longitude"])
    matched += bool(region)
    cities.append([c["name"], cc, region or "Other", round(c["latitude"], 4), round(c["longitude"], 4),
                   int(c["population"])])
cities.sort(key=lambda r: -r[5])
out = Path(__file__).resolve().parent.parent / "travel_planner" / "data" / "world.json.gz"
with gzip.open(out, "wt", encoding="utf-8") as f:
    json.dump({"countries": countries, "cities": cities}, f, ensure_ascii=False, separators=(",", ":"))
print(f"{len(countries)} countries, {len(cities)} cities ({matched} with region) -> {out} "
      f"({out.stat().st_size // 1024} KB)")
