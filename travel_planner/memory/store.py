"""Persistent preference memory (SQLite key-value store, no server needed)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, kind, value)
);
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, request TEXT,
    destination TEXT, total INTEGER, budget INTEGER, itinerary TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, trip_id INTEGER,
    rating INTEGER, comment TEXT, created_at REAL
);
"""


class MemoryStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or settings.memory_db
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    # preferences ---------------------------------------------------------------
    def get_preferences(self, user_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT kind, value, weight FROM preferences WHERE user_id=? AND weight > 0 ORDER BY weight DESC",
            (user_id,)).fetchall()
        prefs: dict = {"likes": [], "dislikes": [], "notes": []}
        for kind, value, _ in rows:
            prefs.setdefault(kind, []).append(value)
        return prefs

    def upsert_preference(self, user_id: str, kind: str, value: str, delta: float = 1.0) -> None:
        value = value.strip().lower()
        if not value:
            return
        opposite = {"likes": "dislikes", "dislikes": "likes"}.get(kind)
        if opposite:   # a new like overrides an old dislike and vice versa
            self.conn.execute("DELETE FROM preferences WHERE user_id=? AND kind=? AND value=?",
                              (user_id, opposite, value))
        self.conn.execute(
            """INSERT INTO preferences (user_id, kind, value, weight, updated_at) VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, kind, value) DO UPDATE SET weight = weight + ?, updated_at = ?""",
            (user_id, kind, value, delta, time.time(), delta, time.time()))
        self.conn.commit()

    def forget(self, user_id: str) -> None:
        self.conn.execute("DELETE FROM preferences WHERE user_id=?", (user_id,))
        self.conn.commit()

    # trips + feedback ----------------------------------------------------------
    def save_trip(self, user_id: str, request: str, destination: str, total: int, budget: int,
                  itinerary: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO trips (user_id, request, destination, total, budget, itinerary, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, request, destination, total, budget, json.dumps(itinerary, ensure_ascii=False), time.time()))
        self.conn.commit()
        return int(cur.lastrowid)

    def past_trips(self, user_id: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, request, destination, total, budget, created_at FROM trips WHERE user_id=? "
            "ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(zip(["id", "request", "destination", "total", "budget", "created_at"], r)) for r in rows]

    def add_feedback(self, user_id: str, trip_id: Optional[int], rating: int, comment: str = "") -> None:
        self.conn.execute("INSERT INTO feedback (user_id, trip_id, rating, comment, created_at) VALUES (?,?,?,?,?)",
                          (user_id, trip_id, int(rating), comment, time.time()))
        self.conn.commit()

    def ratings(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT rating FROM feedback").fetchall()]


_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def set_store(store: MemoryStore) -> None:
    global _store
    _store = store
