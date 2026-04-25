# backend/agents/scout.py
"""
Scout Agent — Event Discovery
Pulls live events from Ticketmaster, GNews, and Foursquare.
Falls back to Gemini LLM suggestions when external APIs return nothing.
Filters by user preference tags and returns a ranked event list.
Writes to: state["events"]
"""
import asyncio
import json
import logging
import os
import re
import httpx
from datetime import datetime, timezone
from difflib import SequenceMatcher

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable
from .state import PlannerState, Event
from .llm import get_llm, ainvoke_with_fallback

load_dotenv()
logger = logging.getLogger(__name__)


# ── API config ────────────────────────────────────────────────────────────────

TICKETMASTER_KEY = os.getenv("TICKETMASTER_API_KEY")
GNEWS_KEY        = os.getenv("GNEWS_API_KEY")
FOURSQUARE_KEY   = os.getenv("FOURSQUARE_API_KEY")

TM_BASE    = "https://app.ticketmaster.com/discovery/v2"
GNEWS_BASE = "https://gnews.io/api/v4"
FSQ_BASE   = "https://api.foursquare.com/v3"

CATEGORY_MAP = {
    # Ticketmaster segment names → our tags
    "Music": ["music", "concert", "jazz", "live"],
    "Arts & Theatre": ["art", "theatre", "museum", "culture"],
    "Sports": ["sports", "game", "outdoor"],
    "Food & Drink": ["food", "street food", "dining"],
    "Family": ["family", "kids"],
    "Film": ["film", "cinema"],
    "Miscellaneous": ["festival", "other"],
}


# ── Geocoding helper (city → lat/lng) ────────────────────────────────────────

# Hardcoded fallback for common demo cities (zero API calls needed)
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "new york":      (40.7128, -74.0060),
    "san francisco": (37.7749, -122.4194),
    "los angeles":   (34.0522, -118.2437),
    "chicago":       (41.8781, -87.6298),
    "london":        (51.5074, -0.1278),
    "paris":         (48.8566,  2.3522),
    "tokyo":         (35.6762, 139.6503),
    "sydney":        (-33.8688, 151.2093),
    "berlin":        (52.5200, 13.4050),
    "mumbai":        (19.0760, 72.8777),
    "dubai":         (25.2048, 55.2708),
    "singapore":     (1.3521,  103.8198),
    "toronto":       (43.6532, -79.3832),
    "seoul":         (37.5665, 126.9780),
    "bangkok":       (13.7563, 100.5018),
    "rome":          (41.9028, 12.4964),
    "amsterdam":     (52.3676,  4.9041),
    "barcelona":     (41.3874,  2.1686),
    "mexico city":   (19.4326, -99.1332),
    "austin":        (30.2672, -97.7431),
}


async def geocode_city(city: str, client: httpx.AsyncClient) -> tuple[float, float]:
    """
    Returns (lat, lng) for a city string.
    Priority: hardcoded fallback → OpenRouteService geocode API → error.
    """
    # 1. Check hardcoded table first (instant, no API call)
    city_lower = city.strip().lower()
    for key, coords in _CITY_COORDS.items():
        if key in city_lower or city_lower in key:
            logger.info("[Scout] Geocoded %r from local table: %s", city, coords)
            return coords

    # 2. Try OpenRouteService geocoding (we already have the API key)
    ors_key = os.getenv("OPENROUTESERVICE_API_KEY")
    if ors_key:
        try:
            r = await client.get(
                "https://api.openrouteservice.org/geocode/search",
                params={"api_key": ors_key, "text": city, "size": 1},
                timeout=8.0,
            )
            r.raise_for_status()
            features = r.json().get("features", [])
            if features:
                coords = features[0]["geometry"]["coordinates"]  # [lng, lat]
                logger.info("[Scout] Geocoded %r via ORS: (%s, %s)", city, coords[1], coords[0])
                return float(coords[1]), float(coords[0])
        except Exception as exc:
            logger.warning("[Scout] ORS geocoding failed for %r: %s", city, exc)

    raise ValueError(f"Could not geocode city: {city}")


# ── Preference scoring ────────────────────────────────────────────────────────

def relevance_score(event_tags: list[str], user_prefs: list[str]) -> float:
    """
    Fuzzy match event tags against user preferences.
    Returns 0.0–1.0. Direct match = 1.0, partial = 0.4–0.8.
    """
    if not user_prefs:
        return 0.5  # No preference → neutral
    score = 0.0
    for pref in user_prefs:
        for tag in event_tags:
            ratio = SequenceMatcher(None, pref.lower(), tag.lower()).ratio()
            score = max(score, ratio)
    return round(score, 3)


# ── Ticketmaster fetch ────────────────────────────────────────────────────────

@traceable(name="scout_fetch_ticketmaster")
async def fetch_ticketmaster(
    city: str, lat: float, lng: float, date: str,
    user_prefs: list[str], client: httpx.AsyncClient
) -> list[Event]:
    if not TICKETMASTER_KEY:
        return []

    params = {
        "apikey": TICKETMASTER_KEY,
        "latlong": f"{lat},{lng}",
        "radius": "25",
        "unit": "miles",
        "startDateTime": f"{date}T00:00:00Z",
        "endDateTime":   f"{date}T23:59:59Z",
        "size": "30",
        "sort": "relevance,desc",
    }

    try:
        r = await client.get(f"{TM_BASE}/events.json", params=params, timeout=10.0)
        r.raise_for_status()
        raw = r.json().get("_embedded", {}).get("events", [])
    except Exception as exc:
        logger.warning("[Scout] Ticketmaster fetch failed: %s", exc)
        return []

    events: list[Event] = []
    for e in raw:
        try:
            # Venue coordinates
            venues = e.get("_embedded", {}).get("venues", [{}])
            venue = venues[0] if venues else {}
            loc = venue.get("location", {})
            ev_lat = float(loc.get("latitude", lat))
            ev_lng = float(loc.get("longitude", lng))

            # Price (take minimum if range available)
            prices = e.get("priceRanges", [])
            cost = float(prices[0]["min"]) if prices else 0.0

            # Category tags
            seg = e.get("classifications", [{}])[0]
            segment_name = seg.get("segment", {}).get("name", "Miscellaneous")
            genre_name   = seg.get("genre", {}).get("name", "")
            tags = CATEGORY_MAP.get(segment_name, ["other"])
            if genre_name:
                tags = tags + [genre_name.lower()]

            events.append(Event(
                name=e.get("name", "Untitled Event"),
                time=e.get("dates", {}).get("start", {}).get("localTime", "TBD"),
                location=venue.get("name", "Venue TBD"),
                address=f"{venue.get('address', {}).get('line1', '')}, "
                        f"{venue.get('city', {}).get('name', city)}",
                lat=ev_lat,
                lng=ev_lng,
                cost=cost,
                category=tags[0] if tags else "other",
                source="ticketmaster",
                url=e.get("url", ""),
                relevance_score=relevance_score(tags, user_prefs),
            ))
        except (KeyError, ValueError, IndexError):
            continue

    return events


# ── GNews fetch ───────────────────────────────────────────────────────────────

@traceable(name="scout_fetch_gnews")
async def fetch_gnews(
    city: str, date: str, user_prefs: list[str], client: httpx.AsyncClient
) -> list[Event]:
    """
    GNews doesn't have event listings — we mine it for local happenings.
    Returns quasi-events (news items as points of interest).
    """
    if not GNEWS_KEY:
        return []

    pref_query = " OR ".join(user_prefs[:3]) if user_prefs else "events"
    query = f"{city} {pref_query} today"

    params = {
        "q": query,
        "lang": "en",
        "max": "10",
        "apikey": GNEWS_KEY,
    }

    try:
        r = await client.get(f"{GNEWS_BASE}/search", params=params, timeout=10.0)
        r.raise_for_status()
        articles = r.json().get("articles", [])
    except Exception as exc:
        logger.warning("[Scout] GNews fetch failed: %s", exc)
        return []

    events: list[Event] = []
    for a in articles:
        title = a.get("title", "")
        # Only keep articles that look like events (rough filter)
        event_words = ["festival", "concert", "market", "fair", "show",
                       "exhibit", "performance", "tour", "race", "game", "event"]
        if not any(w in title.lower() for w in event_words):
            continue

        events.append(Event(
            name=title[:80],
            time="See article",
            location=city,
            address=city,
            lat=0.0,   # GNews has no coords — Navigator will skip these legs
            lng=0.0,
            cost=0.0,
            category="other",
            source="gnews",
            url=a.get("url", ""),
            relevance_score=relevance_score(
                [w for w in title.lower().split() if len(w) > 4],
                user_prefs
            ),
        ))

    return events


# ── Foursquare fetch ──────────────────────────────────────────────────────────

@traceable(name="scout_fetch_foursquare")
async def fetch_foursquare(
    city: str, lat: float, lng: float,
    user_prefs: list[str], client: httpx.AsyncClient
) -> list[Event]:
    """
    Pulls top venues from Foursquare Places API.
    Categories biased toward user preferences.
    """
    if not FOURSQUARE_KEY:
        return []

    # Map preference tags to Foursquare category IDs
    FSQ_CATS = {
        "food":        "13000",   # Food
        "street food": "13306",   # Street food
        "jazz":        "10032",   # Jazz club
        "music":       "10032",   # Music venue
        "art":         "10027",   # Art museum
        "museum":      "10027",
        "outdoor":     "16000",   # Outdoors
        "coffee":      "13035",   # Coffee shop
        "bar":         "13003",
    }

    cat_ids = list({FSQ_CATS[p] for p in user_prefs if p in FSQ_CATS})
    if not cat_ids:
        cat_ids = ["13000", "10032"]  # Default: food + music

    params = {
        "ll": f"{lat},{lng}",
        "radius": "8000",          # 5 miles in meters
        "categories": ",".join(cat_ids[:3]),
        "sort": "RATING",
        "limit": "15",
        "fields": "name,location,categories,rating,price,website",
    }

    headers = {
        "Authorization": FOURSQUARE_KEY,
        "Accept": "application/json",
    }

    try:
        r = await client.get(
            f"{FSQ_BASE}/places/search",
            params=params, headers=headers, timeout=10.0
        )
        r.raise_for_status()
        places = r.json().get("results", [])
    except Exception as exc:
        logger.warning("[Scout] Foursquare fetch failed: %s", exc)
        return []

    events: list[Event] = []
    for p in places:
        loc = p.get("location", {})
        cats = [c.get("name", "").lower() for c in p.get("categories", [])]
        price_tier = p.get("price", 2)
        # Rough cost estimate from Foursquare price tier (1–4)
        cost_est = {1: 15.0, 2: 30.0, 3: 60.0, 4: 100.0}.get(price_tier, 30.0)

        events.append(Event(
            name=p.get("name", "Unknown Venue"),
            time="Flexible",
            location=p.get("name", ""),
            address=loc.get("formatted_address", city),
            lat=loc.get("lat", lat),
            lng=loc.get("lng", lng),
            cost=cost_est,
            category=cats[0] if cats else "food",
            source="foursquare",
            url=p.get("website", ""),
            relevance_score=relevance_score(cats, user_prefs),
        ))

    return events


# ── Gemini LLM fallback ───────────────────────────────────────────────────────

FALLBACK_SYSTEM = """\
You are a local city guide AI. The user wants to spend a day in a specific city.
Generate a list of 6-8 real, well-known places and events that a visitor could
realistically enjoy. Mix categories: food, culture, music, sightseeing, markets.

Return ONLY a JSON array of objects, no markdown fences. Each object:
{
  "name": "Place or Event Name",
  "time": "10:00 AM",
  "location": "Venue or Area Name",
  "address": "Approximate address",
  "lat": 0.0,
  "lng": 0.0,
  "cost": 15.00,
  "category": "food",
  "url": ""
}
Use realistic coordinates for the city. Cost in USD. Categories: food, music, art, culture, outdoor, market, other.
"""


@traceable(name="scout_fallback_gemini")
async def _fallback_gemini(
    city: str, lat: float, lng: float, user_prefs: list[str], budget: float
) -> list[Event]:
    """Ask Gemini to generate plausible local events when APIs return nothing."""
    pref_str = ", ".join(user_prefs) if user_prefs else "general sightseeing and food"
    prompt = (
        f"City: {city} (approx coords: {lat}, {lng})\n"
        f"User preferences: {pref_str}\n"
        f"Budget: ${budget:.0f}\n\n"
        f"Generate 6-8 real, well-known places/events for a day trip. Return JSON array only."
    )

    try:
        llm = get_llm(temperature=0.9)
        response = await ainvoke_with_fallback(llm, [
            SystemMessage(content=FALLBACK_SYSTEM),
            HumanMessage(content=prompt),
        ], temperature=0.9)
        text = response.content.strip()

        # Extract JSON array from response
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            raw = json.loads(match.group(0))
        else:
            raw = json.loads(text)

        events: list[Event] = []
        for item in raw:
            events.append(Event(
                name=item.get("name", "Unknown"),
                time=item.get("time", "Flexible"),
                location=item.get("location", city),
                address=item.get("address", city),
                lat=float(item.get("lat", lat)),
                lng=float(item.get("lng", lng)),
                cost=float(item.get("cost", 0.0)),
                category=item.get("category", "other"),
                source="gemini",
                url=item.get("url", ""),
                relevance_score=relevance_score(
                    [item.get("category", "other")], user_prefs
                ),
            ))

        logger.info("[Scout] Gemini fallback generated %d events for %s", len(events), city)
        return events

    except Exception as exc:
        logger.error("[Scout] Gemini fallback failed: %s", exc)
        return []


# ── Main Scout node ───────────────────────────────────────────────────────────

@traceable(name="scout_node")
async def scout_node(state: PlannerState) -> dict:
    """
    LangGraph node. Reads: city, preferences, date, budget.
    Writes: events (sorted by relevance_score desc, max 20).
    Falls back to Gemini LLM suggestions when all external APIs return nothing.
    """
    city    = state["city"]
    prefs   = state.get("preferences", [])
    date    = state.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    budget  = state.get("budget", 100.0)
    errors  = list(state.get("errors", []))

    async with httpx.AsyncClient() as client:
        # Geocode city first (all APIs need lat/lng)
        try:
            lat, lng = await geocode_city(city, client)
        except Exception as e:
            errors.append(f"Scout: geocoding failed — {e}")
            return {"events": [], "errors": errors}

        # Fan out to all 3 sources concurrently
        tm_task  = fetch_ticketmaster(city, lat, lng, date, prefs, client)
        gn_task  = fetch_gnews(city, date, prefs, client)
        fsq_task = fetch_foursquare(city, lat, lng, prefs, client)

        results = await asyncio.gather(tm_task, gn_task, fsq_task, return_exceptions=True)

    all_events: list[Event] = []
    source_names = ["Ticketmaster", "GNews", "Foursquare"]
    for name, result in zip(source_names, results):
        if isinstance(result, Exception):
            errors.append(f"Scout: {name} fetch failed — {result}")
        else:
            all_events.extend(result)

    # ── Gemini fallback when all APIs return nothing ──────────────────────
    if not all_events:
        logger.warning(
            "[Scout] All external APIs returned 0 events for %s — using Gemini fallback", city
        )
        errors.append("Scout: all external APIs returned 0 events — using Gemini suggestions")
        fallback_events = await _fallback_gemini(city, lat, lng, prefs, budget)
        all_events.extend(fallback_events)

    # Deduplicate by name similarity (Levenshtein-ish)
    seen: list[str] = []
    deduped: list[Event] = []
    for ev in all_events:
        name_lower = ev["name"].lower()
        if not any(SequenceMatcher(None, name_lower, s).ratio() > 0.8 for s in seen):
            seen.append(name_lower)
            deduped.append(ev)

    # Sort by relevance, cap at 20 events for Curator's context window
    ranked = sorted(deduped, key=lambda e: e["relevance_score"], reverse=True)[:20]

    return {
        "events": ranked,
        "errors": errors,
    }