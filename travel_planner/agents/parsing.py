"""Request + preference parsing. Rule-based extraction that the LLM path refines or falls back to."""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from .. import world
from ..config import settings

# preference tag -> words that signal it
PREF_KEYWORDS: dict[str, list[str]] = {
    "food": ["food", "foodie", "eat", "eating", "cuisine", "culinary", "restaurant", "street food", "local dishes"],
    "seafood": ["seafood", "raw fish", "sashimi", "fish market"],
    "hiking": ["hike", "hiking", "trek", "trekking", "climb", "climbing", "mountain", "trail"],
    "beach": ["beach", "beaches", "seaside", "ocean", "swim"],
    "history": ["history", "historic", "historical", "palace", "heritage", "temple"],
    "culture": ["culture", "cultural", "traditional", "local life"],
    "art": ["art", "gallery", "galleries", "exhibition"],
    "shopping": ["shopping", "shop", "boutique", "mall"],
    "nightlife": ["nightlife", "bar", "bars", "club", "clubs", "night out", "party", "drinks"],
    "nature": ["nature", "outdoors", "outdoor", "park", "parks", "scenery"],
    "family": ["kids", "children", "family", "child"],
    "relax": ["relax", "relaxing", "spa", "chill", "slow"],
    "view": ["view", "views", "sunset", "viewpoint", "photo", "photos", "scenic"],
    "animals": ["animals", "zoo", "aquarium", "wildlife", "safari"],
    "architecture": ["architecture", "buildings", "landmarks", "skyline"],
    "theme_park": ["theme park", "amusement park", "rides", "disneyland"],
}
NEGATIONS = ["no", "not", "avoid", "avoids", "avoiding", "hate", "hates", "dislike", "dislikes", "without",
             "skip", "never", "don't", "dont", "doesn't", "can't", "cannot", "except", "nothing"]

CURRENCY_WORDS = {"won": "KRW", "원": "KRW", "₩": "KRW", "$": "USD", "dollar": "USD", "€": "EUR", "euro": "EUR",
                  "yen": "JPY", "¥": "JPY", "£": "GBP", "pound": "GBP", "yuan": "CNY", "rmb": "CNY",
                  "rupee": "INR", "baht": "THB", "lira": "TRY", "dirham": "AED", "riyal": "SAR",
                  "ringgit": "MYR", "rupiah": "IDR", "peso": "MXN", "real": "BRL", "reais": "BRL", "franc": "CHF",
                  "dong": "VND", "rand": "ZAR", "₹": "INR", "฿": "THB", "₺": "TRY", "₫": "VND"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}


def extract_preferences(text: str) -> dict:
    """Find likes/dislikes in free text, e.g. 'I love food but avoid hiking'."""
    likes, dislikes = set(), set()
    low = text.lower()
    clauses = re.split(r"[.;!?\n]|,\s*(?:but|and)?|\bbut\b|\bhowever\b", low)
    for clause in clauses:
        for tag, kws in PREF_KEYWORDS.items():
            for kw in kws:
                m = re.search(rf"\b{re.escape(kw)}\b", clause)
                if not m:
                    continue
                before = re.findall(r"[a-z']+", clause[:m.start()])[-4:]
                negated = any(w in NEGATIONS for w in before) or re.search(r"\b(not into|not a fan of)\b",
                                                                            clause[:m.start()])
                (dislikes if negated else likes).add(tag)
                break
    likes -= dislikes
    return {"likes": sorted(likes), "dislikes": sorted(dislikes)}


def _number(raw: str, suffix: Optional[str]) -> float:
    value = float(raw.replace(",", ""))
    suffix = (suffix or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix in ("m", "million", "mil"):
        value *= 1_000_000
    elif suffix == "만":
        value *= 10_000
    return value


def _currency_pattern() -> str:
    codes = "|".join(c.lower() for c in world.currencies())
    words = "|".join(re.escape(w) + ("s?" if w.isalpha() else "") for w in sorted(CURRENCY_WORDS, key=len, reverse=True))
    return rf"(\b(?:{codes})\b|{words})"


def _currency_code(token: str) -> str:
    t = token.lower()
    if t.upper() in world.USD_RATES:
        return t.upper()
    return CURRENCY_WORDS.get(t) or CURRENCY_WORDS.get(t.rstrip("s")) or settings.default_currency


def parse_budget(text: str) -> tuple[Optional[float], Optional[str]]:
    low = text.lower()
    cur = _currency_pattern()
    num = r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|million|mil|만)?\b"
    m = re.search(num + r"\s*" + cur, low)
    if m:
        return _number(m.group(1), m.group(2)), _currency_code(m.group(3))
    m = re.search(cur + r"\s*" + num, low)
    if m:
        return _number(m.group(2), m.group(3)), _currency_code(m.group(1))
    m = re.search(r"budget\D{0,15}" + num, low)
    if m:
        return _number(m.group(1), m.group(2)), None
    return None, None


def parse_days(text: str) -> Optional[int]:
    low = text.lower()
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "a": 1}
    m = re.search(r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*-?\s*(day|days|night|nights)\b", low)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]
        return n + 1 if m.group(2).startswith("night") else n
    if "weekend" in low:
        return 2
    if re.search(r"\b(a|one)\s+week\b", low):
        return 7
    if re.search(r"\btwo\s+weeks\b", low):
        return 14
    return None


def parse_travelers(text: str) -> Optional[int]:
    low = text.lower()
    m = re.search(r"(\d+)\s*(people|persons|travell?ers|adults|of us|friends|pax|guests)", low)
    if m:
        return int(m.group(1))
    if re.search(r"\b(couple|for two|two of us|with my (wife|husband|partner|girlfriend|boyfriend))\b", low):
        return 2
    if re.search(r"\b(solo|alone|myself|by myself)\b", low):
        return 1
    return None


def parse_places(text: str) -> tuple[Optional[dict], Optional[dict]]:
    """Return (destination, origin) as world-city dicts, using names mentioned in the text."""
    hits = world.find_place_in_text(text)
    origin = None
    for h in hits:
        if re.search(rf"\bfrom\s+(?:the\s+)?{re.escape(h['name'])}", text, re.I):
            origin = h
            break
    dest = next((h for h in hits if h is not origin), None)
    return dest, origin


def parse_start_date(text: str, today: Optional[dt.date] = None) -> Optional[dt.date]:
    today = today or dt.date.today()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    low = text.lower()
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})", low) or \
        re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")", low)
    if m:
        a, b = m.group(1), m.group(2)
        month, day = (MONTHS[a], int(b)) if a in MONTHS else (MONTHS[b], int(a))
        d = dt.date(today.year, month, day)
        return d if d >= today else dt.date(today.year + 1, month, day)
    if "tomorrow" in low:
        return today + dt.timedelta(days=1)
    if "next week" in low:
        return today + dt.timedelta(days=7 - today.weekday())
    return None


def rule_based_parse(text: str) -> dict:
    budget, currency = parse_budget(text)
    dest, origin = parse_places(text)
    # "rupees" / "dollars" / "pesos" are ambiguous: prefer the home (origin) or destination country's one
    families = {"rupee": {"INR", "PKR", "LKR", "NPR"}, "dollar": {"USD", "AUD", "CAD", "SGD", "NZD", "HKD", "TWD"},
                "peso": {"MXN", "ARS", "CLP", "COP", "PHP", "UYU"}}
    for word, codes in families.items():
        if re.search(rf"\b{word}s?\b", text.lower()):
            for place in (origin, dest):
                local = world.country(place["country"]).get("currency") if place else None
                if local in codes:
                    currency = local
                    break
            break
    return {
        "destination": dest["name"] if dest else None,
        "destination_place": dest,
        "origin": origin["name"] if origin else None,
        "origin_place": origin,
        "days": parse_days(text),
        "budget": budget,
        "currency": currency,
        "travelers": parse_travelers(text),
        "start_date": parse_start_date(text),
        **extract_preferences(text),
    }


def to_krw(amount: float, currency: str) -> int:
    """Amount in `currency` -> internal base currency."""
    return world.to_base(amount, currency)


def from_krw(amount: float, currency: str) -> float:
    return world.from_base(amount, currency)
