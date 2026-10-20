"""World geography, price levels and currencies.

* Countries, regions (states / provinces) and ~34,000 cities with population >= 15,000 come from
  GeoNames (CC-BY 4.0), bundled offline in data/world.json.gz.
* Price levels are approximate cost-of-living multipliers (South Korea = 1.0) used to scale
  every estimated price (meals, hotels, tickets, transport) to the destination.
* Currencies: live rates from open.er-api.com (free, no key) when reachable, otherwise a fixed
  table of approximate rates.

Internally all prices are kept in one base currency (KRW, because the curated catalog is in KRW)
and converted to the traveller's currency for display.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

import requests

from .config import settings

log = logging.getLogger(__name__)
DATA = Path(__file__).resolve().parent / "data" / "world.json.gz"
BASE = "KRW"

# --------------------------------------------------------------------------- geography


@lru_cache(maxsize=1)
def _data() -> dict:
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        raw = json.load(f)
    cities = [{"name": c[0], "country": c[1], "region": c[2], "lat": c[3], "lon": c[4], "population": c[5]}
              for c in raw["cities"]]
    return {"countries": raw["countries"], "cities": cities}


def _fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()


def continents() -> list[str]:
    return sorted({c["continent"] for c in _data()["countries"].values()})


def countries(continent: Optional[str] = None) -> list[dict]:
    """[{iso, name, continent, currency, capital}] sorted by name."""
    out = [{"iso": iso, **c} for iso, c in _data()["countries"].items()
           if not continent or c["continent"] == continent]
    return sorted(out, key=lambda c: c["name"])


def country(iso: str) -> dict:
    c = _data()["countries"].get((iso or "").upper())
    return {"iso": iso.upper(), **c} if c else {}


def country_by_name(name: str) -> Optional[dict]:
    n = _fold(name or "").strip()
    for c in countries():
        if _fold(c["name"]) == n or c["iso"].lower() == n:
            return c
    return None


def regions(iso: str) -> list[str]:
    """Regions (states / provinces) of a country, biggest population first."""
    pop: dict[str, int] = {}
    for c in _data()["cities"]:
        if c["country"] == iso:
            pop[c["region"]] = pop.get(c["region"], 0) + c["population"]
    return sorted(pop, key=lambda r: -pop[r])


def cities(iso: str, region: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Cities of a country (optionally of one region), biggest first."""
    out = [c for c in _data()["cities"] if c["country"] == iso and (not region or c["region"] == region)]
    return out[:limit] if limit else out


def find_city(name: str, iso: Optional[str] = None) -> Optional[dict]:
    """Best match for a city name (largest population wins), optionally inside one country."""
    n = _fold(name or "").strip()
    if not n:
        return None
    best = None
    for c in _data()["cities"]:
        if iso and c["country"] != iso:
            continue
        folded = _fold(c["name"])
        if folded == n or folded == f"{n} city":
            return c          # list is sorted by population, first exact match is the biggest
        if best is None and folded.startswith(n) and len(n) >= 4:
            best = c
    return best


def _strip(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def find_place_in_text(text: str) -> list[dict]:
    """Capitalised city or country names mentioned in free text, in order of appearance.

    Matching is case-sensitive ("Nice" the city, not "nice beaches")."""
    plain = _strip(text)
    words = set(re.findall(r"[A-Z][\w'-]*", plain))
    hits: list[tuple[int, dict]] = []
    spans: list[tuple[int, int]] = []
    # the biggest 12k cities are enough for free text; longer names first so "New York City" beats "York"
    for c in sorted(_data()["cities"][:12000], key=lambda c: -len(c["name"])):
        name = _strip(c["name"])
        if name.split()[0] not in words:
            continue
        m = None
        for variant in (name, name[:-5]) if name.endswith(" City") else (name,):   # "Jeju City" -> "Jeju"
            m = re.search(rf"\b{re.escape(variant)}\b", plain)
            if m:
                break
        if not m or any(a <= m.start() < b for a, b in spans) or any(h["name"] == c["name"] for _, h in hits):
            continue
        spans.append((m.start(), m.end()))
        hits.append((m.start(), c))
    for iso, ctry in _data()["countries"].items():       # a country name alone -> its capital
        m = re.search(rf"\b{re.escape(_strip(ctry['name']))}\b", plain)
        if m and ctry.get("capital") and not any(h["country"] == iso for _, h in hits):
            cap = find_city(ctry["capital"], iso)
            if cap:
                hits.append((m.start(), dict(cap, inferred_from_country=True)))
    return [c for _, c in sorted(hits, key=lambda x: x[0])]


def distance_km(a: dict, b: dict) -> float:
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- price levels

# Approximate price level for travellers, South Korea = 1.0 (rounded, for estimates only).
PRICE_INDEX = {
    "CH": 1.9, "IS": 1.8, "NO": 1.7, "DK": 1.5, "LU": 1.5, "IL": 1.5, "US": 1.5, "SE": 1.3, "FI": 1.3,
    "GB": 1.4, "IE": 1.4, "AU": 1.35, "NL": 1.3, "NZ": 1.3, "CA": 1.3, "HK": 1.3, "QA": 1.3, "SG": 1.4,
    "FR": 1.25, "BE": 1.25, "AT": 1.25, "AE": 1.25, "DE": 1.2, "IT": 1.1, "JP": 1.0, "KR": 1.0, "SA": 1.0,
    "ES": 0.95, "MT": 0.95, "PT": 0.85, "GR": 0.85, "HR": 0.85, "SI": 0.85, "EE": 0.85, "CY": 0.9,
    "CZ": 0.8, "TW": 0.8, "JO": 0.8, "SK": 0.75, "LT": 0.75, "LV": 0.75, "PL": 0.7, "HU": 0.7, "CL": 0.7,
    "CN": 0.7, "UY": 0.75, "CR": 0.75, "PA": 0.7, "MX": 0.65, "RO": 0.6, "RU": 0.6, "BR": 0.6, "ZA": 0.6,
    "TR": 0.55, "BG": 0.55, "RS": 0.55, "TH": 0.55, "MY": 0.55, "AR": 0.55, "PE": 0.55, "KE": 0.55,
    "TZ": 0.55, "MA": 0.5, "CO": 0.5, "ID": 0.5, "PH": 0.5, "MN": 0.5, "KZ": 0.5, "GE": 0.5, "AM": 0.5,
    "VN": 0.45, "KH": 0.45, "LA": 0.45, "UA": 0.45, "NG": 0.45, "BO": 0.45, "EC": 0.55, "UZ": 0.4,
    "LK": 0.4, "MM": 0.4, "ET": 0.4, "NP": 0.35, "IN": 0.35, "BD": 0.35, "EG": 0.35, "IR": 0.35,
    "PK": 0.3, "AF": 0.3, "KG": 0.4, "TJ": 0.35, "DZ": 0.4, "TN": 0.4, "GH": 0.5, "OM": 0.95, "KW": 1.1,
    "BH": 1.0, "MV": 1.2, "FJ": 0.9, "IQ": 0.5, "LB": 0.7,
}
CONTINENT_INDEX = {"Europe": 1.0, "North America": 0.9, "Oceania": 1.0, "Asia": 0.6, "South America": 0.6,
                   "Africa": 0.5}


def price_index(iso: str) -> float:
    if iso in PRICE_INDEX:
        return PRICE_INDEX[iso]
    return CONTINENT_INDEX.get(country(iso).get("continent", ""), 0.8)


# --------------------------------------------------------------------------- currencies

# Units per 1 USD (approximate, used when live rates are unavailable).
USD_RATES = {
    "USD": 1, "EUR": 0.92, "GBP": 0.79, "JPY": 150, "KRW": 1380, "CNY": 7.2, "INR": 84, "PKR": 280,
    "AUD": 1.52, "CAD": 1.37, "CHF": 0.88, "SGD": 1.34, "HKD": 7.8, "THB": 35, "VND": 25000, "MYR": 4.5,
    "IDR": 16000, "PHP": 57, "TWD": 32, "AED": 3.67, "SAR": 3.75, "QAR": 3.64, "TRY": 34, "RUB": 92,
    "BRL": 5.5, "MXN": 18.5, "ZAR": 18.3, "EGP": 49, "NZD": 1.66, "SEK": 10.6, "NOK": 10.8, "DKK": 6.9,
    "PLN": 4.0, "CZK": 23, "HUF": 365, "AFN": 70, "BDT": 120, "LKR": 300, "NPR": 135, "ILS": 3.7,
    "MAD": 10, "KES": 129, "NGN": 1600, "ARS": 1000, "CLP": 940, "COP": 4100, "PEN": 3.75, "ISK": 138,
    "RON": 4.6, "UAH": 41, "KZT": 480, "UZS": 12700, "MNT": 3400, "KHR": 4100, "LAK": 21800, "MMK": 2100,
    "IRR": 42000, "IQD": 1310, "JOD": 0.71, "KWD": 0.31, "BHD": 0.38, "OMR": 0.38, "GEL": 2.7, "AMD": 390,
    "TND": 3.1, "DZD": 134, "ETB": 120, "TZS": 2700, "GHS": 15, "MVR": 15.4, "FJD": 2.25, "BGN": 1.8,
    "RSD": 108, "HRK": 6.9, "UYU": 40, "BOB": 6.9, "CRC": 515, "PAB": 1, "KGS": 87, "TJS": 10.9,
}


@lru_cache(maxsize=1)
def _live_rates() -> Optional[dict]:
    if settings.fx_mode == "fixed":
        return None
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=settings.http_timeout)
        r.raise_for_status()
        return r.json()["rates"]
    except Exception as exc:  # noqa: BLE001
        log.info("live exchange rates unavailable (%s); using fixed rates", exc)
        return None


def usd_rate(code: str) -> Optional[float]:
    code = (code or "USD").upper()
    live = _live_rates()
    if live and code in live:
        return float(live[code])
    return USD_RATES.get(code)


def currencies() -> list[str]:
    return sorted(USD_RATES)


def to_base(amount: float, currency: str) -> int:
    """Convert an amount in `currency` to the internal base currency (KRW)."""
    rate = usd_rate(currency) or 1.0
    return int(round(amount / rate * usd_rate(BASE)))


def from_base(amount: float, currency: str) -> float:
    rate = usd_rate(currency) or 1.0
    return amount / usd_rate(BASE) * rate


def money(amount_base: float, currency: str) -> str:
    """Format a base-currency amount in the traveller's currency."""
    value = from_base(amount_base, currency)
    decimals = 0 if (usd_rate(currency) or 1) > 20 else 2 if value < 100 else 0
    return f"{value:,.{decimals}f} {currency.upper()}"
