"""
v2_graph.py — LangGraph StateGraph for the V2 City Day Planner pipeline.

Architecture:
  START → scout_node → curator_node → navigator_node → narrator_node → END

This is a TRUE SEQUENTIAL pipeline — each node reads state written by
the previous node. Remove any node and the chain breaks downstream.

Contrast with V1's graph.py which uses asyncio.gather for parallel execution.

Provides two execution modes:
  - run_v2_graph()           — returns final result (blocking)
  - run_v2_graph_streaming() — async generator yielding SSE progress events
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import redis.asyncio as aioredis
from langgraph.graph import END, START, StateGraph

from agents.state import PlannerState
from agents.scout import scout_node
from agents.curator import curator_node
from agents.navigator import navigator_node
from agents.narrator import narrator_node

logger = logging.getLogger(__name__)


# ── Redis cache ───────────────────────────────────────────────────────────────

_REDIS_URL   = os.getenv("REDIS_URL", "redis://localhost:6379")
_CACHE_TTL   = 6 * 3600  # 6 hours
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _redis


def _cache_key(city: str, date: str, preferences: list[str]) -> str:
    raw = f"{city.lower()}|{date}|{','.join(sorted(preferences))}"
    return "geolens:v2:" + hashlib.sha256(raw.encode()).hexdigest()


# ── Graph Construction ────────────────────────────────────────────────────────

def _build_v2_graph() -> object:
    """
    Build the sequential A2A StateGraph.

    Each node is wired as a linear chain:
      START → scout → curator → navigator → narrator → END

    LangGraph automatically passes the accumulated PlannerState
    between nodes. Each node returns a partial state update dict,
    which LangGraph merges into the running state.
    """
    builder = StateGraph(PlannerState)

    # Register all 4 agent nodes
    builder.add_node("scout", scout_node)
    builder.add_node("curator", curator_node)
    builder.add_node("navigator", navigator_node)
    builder.add_node("narrator", narrator_node)

    # Wire the sequential chain
    builder.add_edge(START, "scout")
    builder.add_edge("scout", "curator")
    builder.add_edge("curator", "navigator")
    builder.add_edge("navigator", "narrator")
    builder.add_edge("narrator", END)

    return builder.compile()


_v2_graph = _build_v2_graph()

# Agent node order for step-by-step streaming
_AGENT_NODES = [
    ("scout", scout_node),
    ("curator", curator_node),
    ("navigator", navigator_node),
    ("narrator", narrator_node),
]


# ── Public Entrypoint ─────────────────────────────────────────────────────────

async def run_v2_graph(
    city: str,
    user_goal: str,
    budget: float,
    preferences: list[str],
    date: str | None = None,
) -> dict:
    """
    Invoke the compiled V2 graph and return the final plan payload.

    Args:
        city:        City name (e.g. "San Francisco")
        user_goal:   Raw natural language from user
        budget:      Total USD budget
        preferences: Extracted preference tags
        date:        ISO date string (defaults to today UTC)

    Returns:
        dict with keys: plan, itinerary, logistics, events, errors
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Cache read ────────────────────────────────────────────────────────
    key = _cache_key(city, date, preferences)
    try:
        cached = await _get_redis().get(key)
        if cached:
            logger.info("[V2 Pipeline] Cache hit: city=%r date=%s", city, date)
            return json.loads(cached)
    except Exception as exc:
        logger.warning("[V2 Pipeline] Redis read failed (%s) — running pipeline", exc)

    initial_state: PlannerState = {
        "city": city,
        "user_goal": user_goal,
        "budget": budget,
        "preferences": preferences,
        "date": date,
        "events": [],
        "itinerary": [],
        "logistics": None,
        "plan": None,
        "errors": [],
        "run_stats": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "[V2 Pipeline] Starting: city=%r goal=%r budget=$%.2f prefs=%r",
        city, user_goal, budget, preferences,
    )

    result: PlannerState = await _v2_graph.ainvoke(initial_state)

    logger.info(
        "[V2 Pipeline] Complete: %d events → %d stops → %d legs → plan=%s",
        len(result.get("events", [])),
        len(result.get("itinerary", [])),
        len(result.get("logistics", {}).get("legs", [])) if result.get("logistics") else 0,
        "yes" if result.get("plan") else "no",
    )

    payload = _extract_result(result)

    # ── Cache write ───────────────────────────────────────────────────────
    try:
        await _get_redis().setex(key, _CACHE_TTL, json.dumps(payload, default=str))
        logger.info("[V2 Pipeline] Cached result (TTL 6h): city=%r date=%s", city, date)
    except Exception as exc:
        logger.warning("[V2 Pipeline] Redis write failed (%s) — result not cached", exc)

    return payload


def _extract_result(state: dict) -> dict:
    """Extract the final payload from a PlannerState dict."""
    return {
        "plan": state.get("plan"),
        "itinerary": state.get("itinerary", []),
        "logistics": state.get("logistics"),
        "events": state.get("events", []),
        "errors": state.get("errors", []),
        "run_stats": state.get("run_stats", []),
    }


# ── Streaming Entrypoint (SSE) ────────────────────────────────────────────────

async def run_v2_graph_streaming(
    city: str,
    user_goal: str,
    budget: float,
    preferences: list[str],
    date: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Run the V2 pipeline step-by-step and yield SSE-formatted events.

    Each yield is a complete SSE message string (event + data lines).
    The frontend reads these via EventSource / ReadableStream.

    Events emitted:
      agent_start  — {"agent": "scout"}
      agent_done   — {"agent": "scout", ...summary stats}
      complete     — full DayPlanResponse JSON
      error        — {"message": "..."}
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build initial state
    state: dict = {
        "city": city,
        "user_goal": user_goal,
        "budget": budget,
        "preferences": preferences,
        "date": date,
        "events": [],
        "itinerary": [],
        "logistics": None,
        "plan": None,
        "errors": [],
        "run_stats": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "[V2 Stream] Starting: city=%r goal=%r budget=$%.2f",
        city, user_goal, budget,
    )

    # Run each agent node manually and yield progress
    for agent_name, agent_fn in _AGENT_NODES:
        # Emit start event
        yield _sse_event("agent_start", {"agent": agent_name})

        try:
            # Call the agent node — each returns a partial state dict
            partial = await agent_fn(state)
            # Merge partial into running state
            state.update(partial)

            # Build summary stats for the done event
            stats = {"agent": agent_name}
            if agent_name == "scout":
                stats["event_count"] = len(state.get("events", []))
            elif agent_name == "curator":
                stats["stop_count"] = len(state.get("itinerary", []))
            elif agent_name == "navigator":
                logistics = state.get("logistics")
                stats["leg_count"] = len(logistics.get("legs", [])) if logistics else 0
                stats["budget_ok"] = logistics.get("budget_ok", True) if logistics else True
            elif agent_name == "narrator":
                plan = state.get("plan")
                stats["has_plan"] = plan is not None

            yield _sse_event("agent_done", stats)

        except Exception as exc:
            logger.error("[V2 Stream] %s failed: %s", agent_name, exc, exc_info=True)
            state.setdefault("errors", []).append(f"{agent_name}: {exc}")
            yield _sse_event("agent_error", {"agent": agent_name, "message": str(exc)})
            # Continue to next agent — downstream agents handle missing data gracefully

    # Emit final complete event with full result
    result = _extract_result(state)
    yield _sse_event("complete", result)

    logger.info("[V2 Stream] Complete for %r", city)


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single SSE message string."""
    json_data = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {json_data}\n\n"
