"""
mcp_server.py — geolens-city-tools MCP server

Exposes Scout (event discovery) and Navigator (routing) fetch functions
as MCP tools. Does NOT wrap the full V2 pipeline — these are composable
primitives an LLM agent can call independently.

Run:
    python mcp_server.py                  # stdio transport (Claude Desktop / any MCP client)
    fastmcp dev mcp_server.py             # interactive inspector for development
"""

import httpx
from fastmcp import FastMCP

from agents.scout import geocode_city, fetch_ticketmaster, fetch_gnews, fetch_foursquare
from agents.navigator import fetch_ors_route

mcp = FastMCP("geolens-city-tools")


@mcp.tool()
async def geocode(city: str) -> dict:
    """
    Return {"lat": float, "lng": float} for a city name.

    Checks a local lookup table for 20 common cities first (no API call),
    then falls back to OpenRouteService geocoding. Call this before any
    tool that requires lat/lng.
    """
    async with httpx.AsyncClient() as client:
        lat, lng = await geocode_city(city, client)
    return {"lat": lat, "lng": lng}


@mcp.tool()
async def search_ticketmaster(
    city: str,
    lat: float,
    lng: float,
    date: str,
    preferences: list[str],
) -> list[dict]:
    """
    Search Ticketmaster for live events near a city on a specific date.

    Args:
        city: City name, e.g. "Tokyo"
        lat: Latitude from geocode()
        lng: Longitude from geocode()
        date: ISO date string, e.g. "2025-07-15"
        preferences: Interest tags, e.g. ["jazz", "museum", "food"]

    Returns a list of Event dicts — each has name, time, location, address,
    lat, lng, cost (USD), category, source, url, relevance_score.
    Returns [] if Ticketmaster API key is absent or the request fails.
    """
    async with httpx.AsyncClient() as client:
        events = await fetch_ticketmaster(city, lat, lng, date, preferences, client)
    return [dict(e) for e in events]


@mcp.tool()
async def search_gnews(
    city: str,
    date: str,
    preferences: list[str],
) -> list[dict]:
    """
    Search GNews for local news items that look like events (festivals,
    concerts, markets, shows, exhibitions, etc.).

    Args:
        city: City name, e.g. "Tokyo"
        date: ISO date string, e.g. "2025-07-15"
        preferences: Interest tags used to filter articles, e.g. ["festival", "concert"]

    Returns a list of Event dicts. Note: lat/lng will be 0.0 because GNews
    articles carry no coordinates — Navigator will skip routing for these stops.
    Returns [] if GNews API key is absent or the request fails.
    """
    async with httpx.AsyncClient() as client:
        events = await fetch_gnews(city, date, preferences, client)
    return [dict(e) for e in events]


@mcp.tool()
async def search_foursquare(
    city: str,
    lat: float,
    lng: float,
    preferences: list[str],
) -> list[dict]:
    """
    Search Foursquare Places for top-rated venues matching preference tags.

    Args:
        city: City name, e.g. "Tokyo"
        lat: Latitude from geocode()
        lng: Longitude from geocode()
        preferences: Interest tags, e.g. ["food", "jazz", "museum", "outdoor"]
                     Supported tags: food, street food, jazz, music, art,
                     museum, outdoor, coffee, bar

    Returns a list of Event dicts with venue name, address, coordinates,
    estimated cost (derived from Foursquare price tier), and category.
    Returns [] if Foursquare API key is absent or the request fails.
    """
    async with httpx.AsyncClient() as client:
        events = await fetch_foursquare(city, lat, lng, preferences, client)
    return [dict(e) for e in events]


@mcp.tool()
async def get_route(
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
) -> dict | None:
    """
    Get a driving route between two coordinates via OpenRouteService.

    Args:
        start_lat: Starting point latitude
        start_lng: Starting point longitude
        end_lat:   Destination latitude
        end_lng:   Destination longitude

    Returns {"duration_seconds": int, "distance_meters": float} on success,
    or null if ORS is unavailable, the API key is absent, or the route fails.
    """
    async with httpx.AsyncClient() as client:
        return await fetch_ors_route(start_lng, start_lat, end_lng, end_lat, client)


if __name__ == "__main__":
    mcp.run()
