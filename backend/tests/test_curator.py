"""
Curator agent tests.

1. Happy path      — events in state + LLM returns valid JSON → itinerary built
2. A2A gate        — no events in state → empty itinerary, no LLM call
3. Quota fallback  — LLM returns Groq response (simulates 429 handled internally)
"""
import json
from unittest import mock

from agents.curator import curator_node


_VALID_ITINERARY_JSON = json.dumps({
    "itinerary": [
        {
            "time": "9:00 AM",
            "event_name": "Tsukiji Outer Market",
            "venue": "Tsukiji Market",
            "address": "Tsukiji, Chuo-ku, Tokyo",
            "lat": 35.6654,
            "lng": 139.7707,
            "cost": 20.0,
            "budget_remaining": 80.0,
            "notes": "Food preference match, morning slot keeps rest of day open.",
        },
        {
            "time": "8:00 PM",
            "event_name": "Jazz Night at Blue Note",
            "venue": "Blue Note Tokyo",
            "address": "6-3-16 Minami-Aoyama, Tokyo",
            "lat": 35.6692,
            "lng": 139.7117,
            "cost": 30.0,
            "budget_remaining": 50.0,
            "notes": "Jazz preference match, highest relevance score.",
        },
    ]
})


async def test_curator_happy_path(base_state, sample_events, mock_llm):
    """Events present + Gemini returns valid itinerary JSON → stops built, stats recorded."""
    state = {**base_state, "events": sample_events}
    llm_response = mock_llm(_VALID_ITINERARY_JSON)

    with mock.patch("agents.curator.ainvoke_with_fallback", return_value=llm_response) as m:
        result = await curator_node(state)

    m.assert_awaited_once()
    assert len(result["itinerary"]) == 2
    assert result["itinerary"][0]["event_name"] == "Tsukiji Outer Market"
    assert result["itinerary"][1]["event_name"] == "Jazz Night at Blue Note"

    stat = result["run_stats"][0]
    assert stat["agent"] == "curator"
    assert stat["tokens_used"] == 500
    assert stat["model_used"] == "gemini-2.5-flash"


async def test_curator_a2a_gate_no_events(base_state):
    """No events → returns empty itinerary + error string, LLM never called."""
    with mock.patch("agents.curator.ainvoke_with_fallback") as m:
        result = await curator_node({**base_state, "events": []})

    m.assert_not_awaited()
    assert result["itinerary"] == []
    assert any("no events" in e.lower() for e in result["errors"])
    assert result["run_stats"][0]["model_used"] == "none"


async def test_curator_quota_fallback(base_state, sample_events, mock_llm):
    """ainvoke_with_fallback transparently returns Groq response → itinerary still built."""
    state = {**base_state, "events": sample_events}
    # Simulates what ainvoke_with_fallback returns after internal Gemini→Groq retry
    groq_response = mock_llm(_VALID_ITINERARY_JSON, model="llama-3.3-70b-versatile", tokens=820)

    with mock.patch("agents.curator.ainvoke_with_fallback", return_value=groq_response):
        result = await curator_node(state)

    assert len(result["itinerary"]) == 2
    stat = result["run_stats"][0]
    assert stat["model_used"] == "llama-3.3-70b-versatile"
    assert stat["tokens_used"] == 820
