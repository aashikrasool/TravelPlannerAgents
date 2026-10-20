"""Choices shown in the dropdown menus of the web app and the interactive CLI."""
from __future__ import annotations

INTERESTS = {
    "food": "Food & local cuisine", "seafood": "Seafood", "history": "History & heritage",
    "culture": "Culture & local life", "art": "Art & museums", "architecture": "Architecture & landmarks",
    "nature": "Nature & parks", "beach": "Beaches", "hiking": "Hiking", "view": "Views & photography",
    "shopping": "Shopping", "nightlife": "Nightlife", "relax": "Relaxing & spa", "family": "Family-friendly",
    "animals": "Animals, zoos & aquariums", "theme_park": "Theme parks",
}
STYLES = {"budget": "Budget - hostels, street food", "standard": "Standard - mid-range hotels",
          "comfort": "Comfort - better hotels, nicer restaurants"}
PACES = {"relaxed": "Relaxed - 2 activities a day", "balanced": "Balanced - 3 activities a day",
         "packed": "Packed - up to 4 activities a day"}
DEFAULT_COUNTRY = "KR"
