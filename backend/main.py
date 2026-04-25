"""
main.py — GeoLens Multi-Agent API (FastAPI entry point).

Endpoints:
  GET  /health          — liveness ping
  POST /api/city-info   — V1: run 4 parallel agents for city intelligence cards
  POST /api/day-plan    — V2: sequential A2A pipeline for day planning

CORS is configured to allow the local Next.js frontend (port 3000/3001).
"""

import logging
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graph import run_graph
from v2_graph import run_v2_graph, run_v2_graph_streaming

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─── App & CORS ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="GeoLens Multi-Agent API",
    description="V1: Parallel city intelligence cards. V2: Sequential A2A day planner pipeline.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    # Allow the Next.js dev server and common preview ports
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request / Response Models ────────────────────────────────────────────────

class CityInfoRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=100, examples=["Tokyo"])
    time_state: Literal["day", "night", "auto"] = Field(
        default="day",
        description="Globe display mode — controls LLM context tone.",
    )


class DayPlanRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=100, examples=["San Francisco"])
    user_goal: str = Field(
        ...,
        min_length=1,
        max_length=500,
        examples=["Plan my day in SF — budget $120, I like jazz and street food"],
        description="Natural language day-planning goal from the user.",
    )
    budget: float = Field(default=100.0, gt=0, le=10000, description="Total USD budget.")
    preferences: list[str] = Field(
        default_factory=list,
        examples=[["jazz", "street food", "outdoor"]],
        description="Extracted preference tags.",
    )
    date: str | None = Field(
        default=None,
        examples=["2025-07-15"],
        description="ISO date for event queries. Defaults to today.",
    )

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["utility"])
async def health() -> dict:
    """Liveness check — returns 200 OK when the server is up."""
    return {"status": "ok", "service": "GeoLens Multi-Agent API", "version": "2.0.0"}


@app.post("/api/city-info", tags=["agents"])
async def city_info(request: CityInfoRequest) -> dict:
    """
    Run all 4 agents in parallel for the given city and return the compiled
    city-intelligence object. Response schema exactly matches AgentData in
    geolens-app/lib/types.ts.

    Returns:
      {
        newshound:   { headline, summary, tag, timeAgo }
        gastroGuide: { dish, restaurant, tip, emoji }
        factoid:     { fact, source }
        ledger:      { day: LedgerItem[], night: LedgerItem[] }
      }
    """
    logger.info(
        "▶ /api/city-info  city=%r  time_state=%r",
        request.city,
        request.time_state,
    )
    try:
        result = await run_graph(request.city, request.time_state)
        logger.info("✓ Response ready for %r", request.city)
        return result
    except Exception as exc:
        logger.error("✗ Graph error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Agent graph failed — make sure Ollama is running "
                f"(`ollama serve`) and the model is pulled. Error: {exc}"
            ),
        ) from exc


# ─── V2: Day Planner (Sequential A2A Pipeline) ───────────────────────────────

@app.post("/api/day-plan", tags=["v2-agents"])
async def day_plan(request: DayPlanRequest) -> dict:
    """
    Run the sequential A2A pipeline: Scout -> Curator -> Navigator -> Narrator.

    Each agent reads the previous agent's output from shared LangGraph state.
    The pipeline produces a complete day plan with timeline, budget breakdown,
    map pins, and citations.

    Returns:
      {
        plan:      { summary, reasoning, timeline, map_pins, budget_breakdown, citations }
        itinerary: [{ time, event_name, venue, cost, budget_remaining, notes }]
        logistics: { legs, total_transport_cost, total_event_cost, grand_total, budget_ok }
        events:    [{ name, time, location, cost, category, source, relevance_score }]
        errors:    ["non-fatal error messages from any agent"]
      }
    """
    logger.info(
        ">> /api/day-plan  city=%r  goal=%r  budget=$%.2f  prefs=%r",
        request.city,
        request.user_goal,
        request.budget,
        request.preferences,
    )
    try:
        result = await run_v2_graph(
            city=request.city,
            user_goal=request.user_goal,
            budget=request.budget,
            preferences=request.preferences,
            date=request.date,
        )
        logger.info("OK - Day plan ready for %r", request.city)
        return result
    except Exception as exc:
        logger.error("FAIL - V2 pipeline error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Day planner pipeline failed: {exc}",
        ) from exc


# ─── V2: Day Planner SSE Stream ────────────────────────────────────────────────

@app.post("/api/day-plan/stream", tags=["v2-agents"])
async def day_plan_stream(request: DayPlanRequest):
    """
    SSE streaming version of the day planner.

    Yields real-time progress events as each agent completes:
      agent_start, agent_done, agent_error, complete

    The frontend reads these via ReadableStream / EventSource.
    """
    logger.info(
        ">> /api/day-plan/stream  city=%r  goal=%r  budget=$%.2f",
        request.city,
        request.user_goal,
        request.budget,
    )

    return StreamingResponse(
        run_v2_graph_streaming(
            city=request.city,
            user_goal=request.user_goal,
            budget=request.budget,
            preferences=request.preferences,
            date=request.date,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
        },
    )


# ─── Dev entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
