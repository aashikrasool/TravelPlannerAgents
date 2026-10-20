"""Weather tool: Open-Meteo (free, open-source, no API key) with a deterministic offline fallback."""
from __future__ import annotations

import datetime as dt
import hashlib
import math

import requests

from ..config import settings

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
       71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers", 81: "Rain showers",
       82: "Violent rain showers", 95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail"}


def is_bad_weather(code: int, rain_prob: float | None) -> bool:
    if code >= 61:
        return True
    return code >= 51 and (rain_prob or 0) >= 60


def geocode(name: str) -> dict:
    r = requests.get(GEOCODE_URL, params={"name": name, "count": 1, "language": "en", "format": "json"},
                     timeout=settings.http_timeout)
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise ValueError(f"place not found: {name}")
    top = results[0]
    return {"name": top["name"], "lat": top["latitude"], "lon": top["longitude"], "country": top.get("country")}


def live_forecast(lat: float, lon: float, start: dt.date, days: int) -> list[dict]:
    """Daily forecast from Open-Meteo. Only covers roughly the next 16 days."""
    end = start + dt.timedelta(days=days - 1)
    r = requests.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }, timeout=settings.http_timeout)
    r.raise_for_status()
    d = r.json()["daily"]
    out = []
    for i, day in enumerate(d["time"]):
        code = int(d["weather_code"][i] or 0)
        prob = d.get("precipitation_probability_max", [None] * len(d["time"]))[i]
        out.append({"date": day, "code": code, "description": WMO.get(code, f"Code {code}"),
                    "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
                    "rain_prob": prob, "bad": is_bad_weather(code, prob)})
    return out


def _climate_temp(lat: float | None, day: dt.date) -> float:
    """Rough daily-high climate estimate from latitude and season (both hemispheres)."""
    if lat is None:
        lat = 35.0
    a = abs(lat)
    mean = 30 - 0.42 * a
    amplitude = min(15.0, 0.28 * a)
    peak = 7 if lat >= 0 else 1                       # warmest month: July in the north, January in the south
    return mean + amplitude * math.cos(2 * math.pi * (day.month - peak) / 12)


def mock_forecast(city: str, start: dt.date, days: int, lat: float | None = None) -> list[dict]:
    """Deterministic pseudo-forecast so demos and evaluations are reproducible offline."""
    out = []
    for i in range(days):
        day = start + dt.timedelta(days=i)
        h = int(hashlib.md5(f"{city.lower()}|{day.isoformat()}".encode()).hexdigest(), 16)
        roll = h % 100
        if roll < 30:
            code, prob = (63 if roll < 15 else 61), 70 + roll % 25
        elif roll < 55:
            code, prob = 3, 20 + roll % 20
        else:
            code, prob = (0 if roll % 2 else 1), roll % 15
        tmax = round(_climate_temp(lat, day) + (h % 5) - 2)
        if tmax <= 1 and code in (61, 63):
            code = 71 if code == 61 else 73          # cold enough to snow
        out.append({"date": day.isoformat(), "code": code, "description": WMO[code],
                    "tmax": tmax, "tmin": tmax - 6 - (h % 3), "rain_prob": prob,
                    "bad": is_bad_weather(code, prob)})
    return out


def get_forecast(city: str, lat: float | None, lon: float | None, start: dt.date, days: int,
                 mode: str | None = None) -> tuple[list[dict], str]:
    """Return (forecast, source). Raises on live failure when mode == 'live'."""
    mode = (mode or settings.weather_mode).lower()
    within_range = 0 <= (start - dt.date.today()).days <= 14
    if mode == "mock" or (mode == "auto" and not within_range):
        return mock_forecast(city, start, days, lat), "offline seasonal estimate"
    if lat is None or lon is None:
        g = geocode(city)
        lat, lon = g["lat"], g["lon"]
    return live_forecast(lat, lon, start, days), "Open-Meteo live forecast"
