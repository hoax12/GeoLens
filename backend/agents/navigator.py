# backend/agents/navigator.py
"""
Navigator Agent — Logistics Planner
READS:  state["itinerary"]  (from Curator)
        state["budget"]
WRITES: state["logistics"]  (Logistics TypedDict)

Mostly deterministic — calls OpenRouteService for real transit times
between itinerary stops. The LLM is used only for a light budget
assessment and mode recommendation.

TRUE A2A DEPENDENCY: Cannot function without Curator's itinerary.
Remove the Curator and Navigator has no venues to route between.
"""

import asyncio
import logging
import os
import time
from math import radians, sin, cos, sqrt, atan2

import httpx
from dotenv import load_dotenv
from langsmith import traceable

from .state import PlannerState, TravelLeg, Logistics, ItineraryStop

load_dotenv()
logger = logging.getLogger(__name__)

ORS_KEY = os.getenv("OPENROUTESERVICE_API_KEY")
ORS_BASE = "https://api.openrouteservice.org/v2"


# ── Haversine fallback ────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine distance in km — used as fallback when ORS fails."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _estimate_duration_and_mode(distance_km: float) -> tuple[int, str, float]:
    """
    Heuristic fallback: estimate travel time, mode, and cost from distance.
    Returns (duration_minutes, mode, estimated_cost_usd).
    """
    if distance_km < 1.0:
        return 12, "walk", 0.0
    elif distance_km < 5.0:
        return int(distance_km * 4), "transit", 2.75  # avg transit fare
    else:
        minutes = int(distance_km * 2.5)  # ~24 km/h avg urban rideshare
        cost = 5.0 + (distance_km * 1.5)  # base fare + per-km
        return minutes, "rideshare", round(cost, 2)


# ── OpenRouteService directions ──────────────────────────────────────────────

@traceable(name="navigator_fetch_ors_route")
async def fetch_ors_route(
    start_lng: float, start_lat: float,
    end_lng: float, end_lat: float,
    client: httpx.AsyncClient,
) -> dict | None:
    """
    Call OpenRouteService Directions API for driving route.
    Returns {duration_seconds, distance_meters} or None on failure.

    Note: ORS uses [lng, lat] order (GeoJSON convention).
    """
    if not ORS_KEY:
        return None

    url = f"{ORS_BASE}/directions/driving-car"
    params = {
        "api_key": ORS_KEY,
        "start": f"{start_lng},{start_lat}",
        "end": f"{end_lng},{end_lat}",
    }

    try:
        r = await client.get(url, params=params, timeout=10.0)
        r.raise_for_status()
        data = r.json()

        # ORS v2 GeoJSON response structure
        features = data.get("features", [])
        if not features:
            return None

        segment = features[0].get("properties", {}).get("segments", [{}])[0]
        return {
            "duration_seconds": segment.get("duration", 0),
            "distance_meters": segment.get("distance", 0),
        }
    except Exception as exc:
        logger.warning("[Navigator] ORS route failed: %s", exc)
        return None


# ── Recommend transport mode ─────────────────────────────────────────────────

def _recommend_mode(distance_km: float, duration_min: int) -> tuple[str, float]:
    """Pick transport mode and estimate cost based on distance."""
    if distance_km < 1.0:
        return "walk", 0.0
    elif distance_km < 6.0:
        return "transit", 2.75
    else:
        cost = 5.0 + (distance_km * 1.5)
        return "rideshare", round(cost, 2)


# ── Build a single travel leg ────────────────────────────────────────────────

@traceable(name="navigator_build_leg")
async def build_travel_leg(
    from_stop: ItineraryStop,
    to_stop: ItineraryStop,
    client: httpx.AsyncClient,
) -> TravelLeg:
    """
    Build a TravelLeg between two consecutive itinerary stops.
    Tries ORS first, falls back to haversine heuristic.
    """
    from_lat, from_lng = from_stop["lat"], from_stop["lng"]
    to_lat, to_lng = to_stop["lat"], to_stop["lng"]

    # Skip routing if either stop has no coordinates (e.g. GNews events)
    has_coords = all(v != 0.0 for v in (from_lat, from_lng, to_lat, to_lng))

    duration_min = 15  # default
    distance_km = 0.0
    mode = "walk"
    cost = 0.0
    instructions = ""

    if has_coords:
        # Try OpenRouteService
        ors_result = await fetch_ors_route(from_lng, from_lat, to_lng, to_lat, client)

        if ors_result:
            duration_min = max(1, int(ors_result["duration_seconds"] / 60))
            distance_km = ors_result["distance_meters"] / 1000.0
            mode, cost = _recommend_mode(distance_km, duration_min)
            instructions = (
                f"{distance_km:.1f} km via {mode} "
                f"(~{duration_min} min)"
            )
        else:
            # Haversine fallback
            distance_km = _haversine_km(from_lat, from_lng, to_lat, to_lng)
            duration_min, mode, cost = _estimate_duration_and_mode(distance_km)
            instructions = (
                f"~{distance_km:.1f} km via {mode} "
                f"(estimated ~{duration_min} min, ORS unavailable)"
            )
    else:
        instructions = "Coordinates unavailable — estimate 15 min transit"
        mode = "transit"
        cost = 2.75

    return TravelLeg(
        from_venue=from_stop["venue"],
        to_venue=to_stop["venue"],
        departure_time=from_stop["time"],
        arrival_time=to_stop["time"],
        duration_minutes=duration_min,
        mode=mode,
        estimated_cost=cost,
        instructions=instructions,
    )


# ── Nearest-neighbor stop reordering ────────────────────────────────────────

def _nearest_neighbor_sort(stops: list[ItineraryStop]) -> list[ItineraryStop]:
    """
    Greedy nearest-neighbor reorder to minimise total route distance.
    The first stop is kept as the morning anchor; the rest are reordered.
    Original time slots are redistributed in the new order so the schedule
    stays chronological even after geographic optimisation.
    """
    if len(stops) <= 2:
        return stops

    with_coords = [s for s in stops if not (s["lat"] == 0.0 and s["lng"] == 0.0)]
    no_coords   = [s for s in stops if s["lat"] == 0.0 and s["lng"] == 0.0]

    if len(with_coords) <= 2:
        return stops

    ordered: list[ItineraryStop] = [with_coords[0]]
    remaining = list(with_coords[1:])
    while remaining:
        last = ordered[-1]
        nearest = min(
            remaining,
            key=lambda s: _haversine_km(last["lat"], last["lng"], s["lat"], s["lng"]),
        )
        ordered.append(nearest)
        remaining.remove(nearest)

    reordered = ordered + no_coords
    original_times = [s["time"] for s in stops]
    return [{**s, "time": original_times[i]} for i, s in enumerate(reordered)]  # type: ignore[return-value]


# ── Main Navigator node ──────────────────────────────────────────────────────

@traceable(name="navigator_node")
async def navigator_node(state: PlannerState) -> dict:
    """
    LangGraph node.
    Reads:  itinerary, budget
    Writes: logistics (Logistics TypedDict)

    TRUE A2A DEPENDENCY: If state["itinerary"] is empty,
    Navigator cannot route — returns an error.
    """
    start     = time.monotonic()
    itinerary = state.get("itinerary", [])
    budget    = state.get("budget", 100.0)
    errors    = list(state.get("errors", []))
    run_stats = list(state.get("run_stats", []))

    # ── A2A gate: require Curator output ──────────────────────────────────
    if not itinerary:
        errors.append("Navigator: no itinerary from Curator — cannot route")
        logger.warning("[Navigator] No itinerary in state — Curator may have failed")
        run_stats.append({"agent": "navigator", "tokens_used": 0, "latency_ms": int((time.monotonic() - start) * 1000), "model_used": "none"})
        return {
            "logistics": Logistics(
                legs=[],
                total_transport_cost=0.0,
                total_event_cost=0.0,
                grand_total=0.0,
                budget_ok=True,
                budget_warning="No itinerary to route.",
            ),
            "errors": errors,
            "run_stats": run_stats,
        }

    # ── Reorder stops geographically before routing ───────────────────────
    ordered = _nearest_neighbor_sort(itinerary)
    if ordered is not itinerary:
        logger.info("[Navigator] Reordered %d stops by nearest-neighbor proximity", len(ordered))

    # ── Build travel legs between consecutive stops ───────────────────────
    legs: list[TravelLeg] = []

    async with httpx.AsyncClient() as client:
        tasks = [
            build_travel_leg(ordered[i], ordered[i + 1], client)
            for i in range(len(ordered) - 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append(
                f"Navigator: leg {i} failed ({ordered[i]['venue']} "
                f"-> {ordered[i+1]['venue']}): {result}"
            )
            legs.append(TravelLeg(
                from_venue=ordered[i]["venue"],
                to_venue=ordered[i + 1]["venue"],
                departure_time=ordered[i]["time"],
                arrival_time=ordered[i + 1]["time"],
                duration_minutes=15,
                mode="transit",
                estimated_cost=3.0,
                instructions="Route calculation failed — estimated 15 min transit",
            ))
        else:
            legs.append(result)

    # ── Compute totals ────────────────────────────────────────────────────
    total_transport = sum(leg["estimated_cost"] for leg in legs)
    total_events = sum(stop["cost"] for stop in ordered)
    grand_total = round(total_transport + total_events, 2)

    budget_ok = grand_total <= budget
    budget_warning = None
    if not budget_ok:
        overage = round(grand_total - budget, 2)
        budget_warning = (
            f"Over budget by ${overage:.2f}. "
            f"Events: ${total_events:.2f}, Transport: ${total_transport:.2f}, "
            f"Total: ${grand_total:.2f} vs Budget: ${budget:.2f}"
        )
    elif grand_total > budget * 0.9:
        remaining = round(budget - grand_total, 2)
        budget_warning = f"Tight budget — only ${remaining:.2f} remaining"

    logistics = Logistics(
        legs=legs,
        total_transport_cost=round(total_transport, 2),
        total_event_cost=round(total_events, 2),
        grand_total=grand_total,
        budget_ok=budget_ok,
        budget_warning=budget_warning,
    )

    logger.info(
        "[Navigator] %d legs, transport=$%.2f, events=$%.2f, total=$%.2f, budget_ok=%s",
        len(legs), total_transport, total_events, grand_total, budget_ok,
    )

    run_stats.append({"agent": "navigator", "tokens_used": 0, "latency_ms": int((time.monotonic() - start) * 1000), "model_used": "none"})
    return {"itinerary": ordered, "logistics": logistics, "errors": errors, "run_stats": run_stats}
