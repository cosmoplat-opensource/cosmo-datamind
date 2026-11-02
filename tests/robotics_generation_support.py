"""Independent task fixtures for strict/draft policy review — DR-061 / DR-062 / DR-065."""
from copy import deepcopy
from functools import lru_cache

from tests.robotics_support import FIXTURES, api


def task(**changes):
    result = {"operation": "pick_place", "part": "Part_A_001", "station": "Station_B",
              "carrier": "Carrier_B_01", "slot": "Slot_B_01", "unsupported_requests": []}
    result.update(changes)
    return result


@lru_cache(maxsize=2)
def _context(mode):
    return api("consumer").load_ontology(FIXTURES / "owl" / f"agent_{mode}.owl", mode=mode)


def context(mode="demo"):
    # Every test owns its graph, including tests that remove administrative evidence.
    return deepcopy(_context(mode))


def assert_no_candidate(result, directory, status):
    assert result["status"] == status
    assert result["candidate_written"] is False
    assert result.get("candidate_path") is None
    assert result["issues"], "A rejection must explain the missing information or unsupported request"
    assert all(item.get("code") and item.get("message") for item in result["issues"])
    assert not list(directory.rglob("*.xml")), "Rejected task wrote an XML artifact"
