"""
Narrator agent tests.

1. Happy path    — full state + LLM returns valid plan JSON → plan populated
2. A2A gate      — no itinerary → stub plan returned, no LLM call
3. LLM failure   — ainvoke_with_fallback raises → deterministic fallback plan built
"""
import json
from unittest import mock

from agents.narrator import narrator_node


_VALID_PLAN_JSON = json.dumps({
    "summary": "Your day in Tokyo starts at Tsukiji market for fresh sushi, then winds up at Blue Note for jazz.",
    "reasoning": "Chose food and jazz stops to match stated preferences within the $100 budget.",
    "timeline": [
        {"time": "9:00 AM", "label": "Tsukiji Outer Market", "type": "event", "icon": "food"},
        {"time": "9:25 AM", "label": "Transit to Blue Note (25 min)", "type": "travel", "icon": "transit"},
        {"time": "8:00 PM", "label": "Jazz Night at Blue Note", "type": "event", "icon": "music"},
    ],
    "map_pins": [
        {"name": "Tsukiji Market", "lat": 35.6654, "lng": 139.7707, "type": "event"},
        {"name": "Blue Note Tokyo", "lat": 35.6692, "lng": 139.7117, "type": "event"},
    ],
    "budget_breakdown": {"events": 50.0, "transport": 2.75, "remaining": 47.25},
    "citations": [
        {"label": "Jazz Night at Blue Note", "url": "https://example.com/jazz", "source": "ticketmaster"}
    ],
})


async def test_narrator_happy_path(base_state, sample_events, sample_itinerary, sample_logistics, mock_llm):
    """Full pipeline state → LLM returns valid JSON → plan has all required fields."""
    state = {
        **base_state,
        "events": sample_events,
        "itinerary": sample_itinerary,
        "logistics": sample_logistics,
    }
    llm_response = mock_llm(_VALID_PLAN_JSON)

    with mock.patch("agents.narrator.ainvoke_with_fallback", return_value=llm_response) as m:
        result = await narrator_node(state)

    m.assert_awaited_once()
    plan = result["plan"]
    assert plan is not None
    assert "Tokyo" in plan["summary"]
    assert len(plan["timeline"]) == 3
    assert len(plan["map_pins"]) == 2
    assert plan["budget_breakdown"]["events"] == 50.0
    assert len(plan["citations"]) == 1

    stat = result["run_stats"][0]
    assert stat["agent"] == "narrator"
    assert stat["tokens_used"] == 500
    assert stat["model_used"] == "gemini-2.5-flash"


async def test_narrator_a2a_gate_no_itinerary(base_state):
    """No itinerary → stub plan returned with error message, LLM never called."""
    with mock.patch("agents.narrator.ainvoke_with_fallback") as m:
        result = await narrator_node({**base_state, "itinerary": []})

    m.assert_not_awaited()
    plan = result["plan"]
    assert plan is not None
    # Stub plan summary acknowledges the missing itinerary
    assert "no itinerary" in plan["summary"].lower() or "could not" in plan["summary"].lower()
    assert any("no itinerary" in e.lower() for e in result["errors"])
    assert result["run_stats"][0]["model_used"] == "none"


async def test_narrator_llm_failure_uses_deterministic_fallback(
    base_state, sample_events, sample_itinerary, sample_logistics
):
    """LLM raises → _build_fallback_plan assembles plan from raw state, no re-raise."""
    state = {
        **base_state,
        "events": sample_events,
        "itinerary": sample_itinerary,
        "logistics": sample_logistics,
    }

    with mock.patch(
        "agents.narrator.ainvoke_with_fallback",
        side_effect=RuntimeError("LLM service unavailable"),
    ):
        result = await narrator_node(state)

    plan = result["plan"]
    assert plan is not None
    # Fallback builds timeline from raw itinerary stops
    assert len(plan["timeline"]) > 0
    assert any("failed" in e.lower() or "llm" in e.lower() for e in result["errors"])
    assert result["run_stats"][0]["model_used"] == "error"
