"""
main.py — GeoLens Multi-Agent API (FastAPI entry point).

Endpoints:
  GET  /health          — liveness ping
  POST /api/city-info   — run 4 agents for a city, return structured JSON

CORS is configured to allow the local Next.js frontend (port 3000/3001).
"""

import logging
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from graph import run_graph

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
    description="Runs 4 parallel LLM agents via LangGraph + Ollama for city intelligence cards.",
    version="1.0.0",
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


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["utility"])
async def health() -> dict:
    """Liveness check — returns 200 OK when the server is up."""
    return {"status": "ok", "service": "GeoLens Multi-Agent API", "version": "1.0.0"}


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


# ─── Dev entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
