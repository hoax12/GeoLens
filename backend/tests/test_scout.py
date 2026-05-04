"""
Scout agent tests.

1. Happy path   — TM + GNews + FSQ all return data → ranked events in state
2. API failure  — all API keys absent → Gemini fallback fires → events returned
3. Geocode fail — unknown city, no ORS key → empty events + error recorded
"""
from unittest import mock

import respx
from httpx import Response

from agents.scout import scout_node


# ── Mock API response payloads ────────────────────────────────────────────────

_TM_RESPONSE = {
    "_embedded": {
        "events": [
            {
                "name": "Jazz Festival Tokyo",
                "dates": {"start": {"localTime": "20:00:00"}},
                "url": "https://ticketmaster.com/event/1",
                "_embedded": {
                    "venues": [{
                        "name": "Shibuya O-East",
                        "address": {"line1": "2-14-8 Maruyamacho"},
                        "city": {"name": "Tokyo"},
                        "location": {"latitude": "35.6609", "longitude": "139.6973"},
                    }]
                },
                "priceRanges": [{"min": 30.0}],
                "classifications": [
                    {"segment": {"name": "Music"}, "genre": {"name": "Jazz"}}
                ],
            }
        ]
    }
}

_GNEWS_RESPONSE = {
    "articles": [
        {
            "title": "Tokyo Jazz Festival returns for summer concert series",
            "url": "https://gnews.io/article/1",
        }
    ]
}

_FSQ_RESPONSE = {
    "results": [
        {
            "name": "Blue Note Tokyo",
            "location": {
                "formatted_address": "6-3-16 Minami-Aoyama, Minato-ku, Tokyo",
                "lat": 35.6692,
                "lng": 139.7117,
            },
            "categories": [{"name": "jazz club"}],
            "price": 2,
            "website": "https://bluenote.co.jp",
        }
    ]
}


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_scout_happy_path(base_state):
    """All 3 APIs return data → events populated and sorted by relevance."""
    with respx.mock:
        respx.get("https://app.ticketmaster.com/discovery/v2/events.json").mock(
            return_value=Response(200, json=_TM_RESPONSE)
        )
        respx.get("https://gnews.io/api/v4/search").mock(
            return_value=Response(200, json=_GNEWS_RESPONSE)
        )
        respx.get("https://api.foursquare.com/v3/places/search").mock(
            return_value=Response(200, json=_FSQ_RESPONSE)
        )

        with (
            mock.patch("agents.scout.TICKETMASTER_KEY", "test_tm"),
            mock.patch("agents.scout.GNEWS_KEY", "test_gn"),
            mock.patch("agents.scout.FOURSQUARE_KEY", "test_fsq"),
        ):
            result = await scout_node(base_state)

    events = result["events"]
    assert len(events) > 0
    # Verify descending relevance order
    scores = [e["relevance_score"] for e in events]
    assert scores == sorted(scores, reverse=True)
    # run_stats must have exactly one scout entry
    assert len(result["run_stats"]) == 1
    assert result["run_stats"][0]["agent"] == "scout"
    assert result["run_stats"][0]["latency_ms"] >= 0


async def test_scout_api_failure_triggers_gemini_fallback(base_state, sample_events):
    """All API keys absent → Gemini fallback called → events still returned."""
    gemini_event = {**sample_events[0], "source": "gemini"}

    with (
        mock.patch("agents.scout.TICKETMASTER_KEY", None),
        mock.patch("agents.scout.GNEWS_KEY", None),
        mock.patch("agents.scout.FOURSQUARE_KEY", None),
        mock.patch("agents.scout._fallback_gemini", return_value=[gemini_event]) as mock_fb,
    ):
        result = await scout_node(base_state)

    mock_fb.assert_awaited_once()
    assert len(result["events"]) == 1
    assert result["events"][0]["source"] == "gemini"
    # An error should note that all external APIs returned nothing
    assert any("0 events" in e or "gemini" in e.lower() for e in result["errors"])


async def test_scout_geocoding_failure_returns_empty(base_state):
    """Geocoding raises → empty events, error recorded, run_stats still appended."""
    with mock.patch(
        "agents.scout.geocode_city",
        side_effect=ValueError("Could not geocode city: Faketown"),
    ):
        result = await scout_node({**base_state, "city": "Faketown"})

    assert result["events"] == []
    assert any("geocod" in e.lower() for e in result["errors"])
    assert len(result["run_stats"]) == 1
    assert result["run_stats"][0]["model_used"] == "none"
