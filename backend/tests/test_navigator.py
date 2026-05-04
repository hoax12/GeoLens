"""
Navigator agent tests.

1. Happy path    — itinerary with coords + ORS returns route → legs built
2. ORS failure   — ORS returns 5xx → haversine fallback used, legs still built
3. A2A gate      — empty itinerary → empty legs + error, no HTTP calls
"""
from unittest import mock

import respx
from httpx import Response

from agents.navigator import navigator_node


_ORS_RESPONSE = {
    "features": [
        {
            "properties": {
                "segments": [{"duration": 900, "distance": 4200}]  # 15 min, 4.2 km
            }
        }
    ]
}

_ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"


async def test_navigator_happy_path(base_state, sample_itinerary):
    """ORS returns a valid route → leg duration and distance derived from real data."""
    with respx.mock:
        respx.get(_ORS_URL).mock(return_value=Response(200, json=_ORS_RESPONSE))

        with mock.patch("agents.navigator.ORS_KEY", "test_ors"):
            result = await navigator_node({**base_state, "itinerary": sample_itinerary})

    logistics = result["logistics"]
    assert logistics is not None
    assert len(logistics["legs"]) == 1

    leg = logistics["legs"][0]
    assert leg["from_venue"] == "Tsukiji Market"
    assert leg["to_venue"] == "Blue Note Tokyo"
    assert leg["duration_minutes"] == 15  # 900s / 60
    assert logistics["total_event_cost"] == 50.0  # 20 + 30
    assert logistics["budget_ok"] is True

    stat = result["run_stats"][0]
    assert stat["agent"] == "navigator"
    assert stat["model_used"] == "none"  # Navigator never calls an LLM


async def test_navigator_ors_failure_uses_haversine(base_state, sample_itinerary):
    """ORS returns 5xx → haversine fallback fills in duration; leg still produced."""
    with respx.mock:
        respx.get(_ORS_URL).mock(return_value=Response(500, text="Internal Server Error"))

        with mock.patch("agents.navigator.ORS_KEY", "test_ors"):
            result = await navigator_node({**base_state, "itinerary": sample_itinerary})

    logistics = result["logistics"]
    assert logistics is not None
    assert len(logistics["legs"]) == 1
    # Haversine path sets instructions with "ORS unavailable" or "estimated"
    instructions = logistics["legs"][0]["instructions"].lower()
    assert "ors unavailable" in instructions or "estimated" in instructions


async def test_navigator_a2a_gate_no_itinerary(base_state):
    """Empty itinerary → logistics has empty legs, error appended, no HTTP calls made."""
    with respx.mock:  # any stray HTTP call would raise inside this block
        result = await navigator_node({**base_state, "itinerary": []})

    assert result["logistics"]["legs"] == []
    assert any("no itinerary" in e.lower() for e in result["errors"])
    assert result["run_stats"][0]["model_used"] == "none"
