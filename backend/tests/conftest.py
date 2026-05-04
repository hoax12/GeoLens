"""
Shared fixtures and helpers for the V2 agent test suite.
"""
import pytest
from unittest.mock import MagicMock


# ── Shared state fixture ──────────────────────────────────────────────────────

@pytest.fixture
def base_state():
    return {
        "city": "Tokyo",
        "user_goal": "Jazz and sushi",
        "budget": 100.0,
        "preferences": ["jazz", "food"],
        "date": "2026-04-25",
        "events": [],
        "itinerary": [],
        "logistics": None,
        "plan": None,
        "errors": [],
        "run_stats": [],
        "started_at": "2026-04-25T00:00:00+00:00",
    }


# ── Shared data fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def sample_events():
    return [
        {
            "name": "Jazz Night at Blue Note",
            "time": "8:00 PM",
            "location": "Blue Note Tokyo",
            "address": "6-3-16 Minami-Aoyama, Minato-ku, Tokyo",
            "lat": 35.6692,
            "lng": 139.7117,
            "cost": 30.0,
            "category": "music",
            "source": "ticketmaster",
            "url": "https://example.com/jazz",
            "relevance_score": 0.95,
        },
        {
            "name": "Tsukiji Outer Market",
            "time": "9:00 AM",
            "location": "Tsukiji Market",
            "address": "Tsukiji, Chuo-ku, Tokyo",
            "lat": 35.6654,
            "lng": 139.7707,
            "cost": 20.0,
            "category": "food",
            "source": "foursquare",
            "url": "https://example.com/tsukiji",
            "relevance_score": 0.85,
        },
    ]


@pytest.fixture
def sample_itinerary():
    return [
        {
            "time": "9:00 AM",
            "event_name": "Tsukiji Outer Market",
            "venue": "Tsukiji Market",
            "address": "Tsukiji, Chuo-ku, Tokyo",
            "lat": 35.6654,
            "lng": 139.7707,
            "cost": 20.0,
            "budget_remaining": 80.0,
            "notes": "Great food stop to match food preference.",
        },
        {
            "time": "8:00 PM",
            "event_name": "Jazz Night at Blue Note",
            "venue": "Blue Note Tokyo",
            "address": "6-3-16 Minami-Aoyama, Minato-ku, Tokyo",
            "lat": 35.6692,
            "lng": 139.7117,
            "cost": 30.0,
            "budget_remaining": 50.0,
            "notes": "Jazz preference match, high relevance score.",
        },
    ]


@pytest.fixture
def sample_logistics():
    return {
        "legs": [
            {
                "from_venue": "Tsukiji Market",
                "to_venue": "Blue Note Tokyo",
                "departure_time": "9:00 AM",
                "arrival_time": "8:00 PM",
                "duration_minutes": 25,
                "mode": "transit",
                "estimated_cost": 2.75,
                "instructions": "Take subway from Tsukiji to Aoyama (~25 min)",
            }
        ],
        "total_transport_cost": 2.75,
        "total_event_cost": 50.0,
        "grand_total": 52.75,
        "budget_ok": True,
        "budget_warning": None,
    }


# ── LLM response mock factory ─────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """Returns a factory that builds fake LangChain AIMessage objects."""
    def _factory(content: str, model: str = "gemini-2.5-flash", tokens: int = 500):
        r = MagicMock()
        r.content = content
        r.usage_metadata = {"total_tokens": tokens}
        r.response_metadata = {"model_name": model}
        return r
    return _factory
