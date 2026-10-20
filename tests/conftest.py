import os
import sys
from pathlib import Path

# deterministic, offline test environment (set before the package is imported)
os.environ["LLM_PROVIDER"] = "none"
os.environ["WEATHER_MODE"] = "mock"
os.environ["PLACES_MODE"] = "mock"
os.environ["MEMORY_DB"] = ":memory:"
os.environ["FX_MODE"] = "fixed"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from travel_planner.llm import set_llm  # noqa: E402
from travel_planner.memory.store import MemoryStore, set_store  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_memory():
    store = MemoryStore(":memory:")
    set_store(store)
    set_llm(None)
    yield store
    set_llm(None)
