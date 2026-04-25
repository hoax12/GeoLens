"""
v2_state.py — LangGraph shared state for the City Day Planner pipeline.

Defines the PlannerState TypedDict that carries data across all 4 agents
in the sequential A2A pipeline: Scout → Curator → Navigator → Narrator.

Each agent reads from previously-written keys and writes new ones.
Remove any agent and the chain breaks — that's the A2A proof.
"""

from __future__ import annotations
from typing import TypedDict, Optional
from datetime import datetime


class Event(TypedDict):
    name: str
    time: str                  # ISO 8601 or human-readable "7:30 PM"
    location: str              # Venue name
    address: str
    lat: float
    lng: float
    cost: float                # USD. 0.0 = free
    category: str              # "music" | "food" | "art" | "sports" | "festival" | "other"
    source: str                # "ticketmaster" | "gnews" | "foursquare"
    url: str                   # Citation link
    relevance_score: float     # 0.0–1.0, set by Scout after preference matching


class ItineraryStop(TypedDict):
    time: str                  # Scheduled time for this stop
    event_name: str
    venue: str
    address: str
    lat: float
    lng: float
    cost: float
    budget_remaining: float
    notes: str                 # Curator's reasoning (e.g. "chosen for jazz + low cost")


class TravelLeg(TypedDict):
    from_venue: str
    to_venue: str
    departure_time: str
    arrival_time: str
    duration_minutes: int
    mode: str                  # "transit" | "walk" | "rideshare"
    estimated_cost: float
    instructions: str          # Turn-by-turn or transit line summary


class Logistics(TypedDict):
    legs: list[TravelLeg]
    total_transport_cost: float
    total_event_cost: float
    grand_total: float
    budget_ok: bool            # grand_total <= user budget
    budget_warning: Optional[str]  # e.g. "Over budget by $12 — dropped venue X"


class Plan(TypedDict):
    summary: str               # Narrator's conversational day summary
    reasoning: str             # Why certain events were chosen/dropped
    timeline: list[dict]       # Merged itinerary + legs for UI rendering
    map_pins: list[dict]       # [{name, lat, lng, type}] for globe
    budget_breakdown: dict     # {events: float, transport: float, remaining: float}
    citations: list[dict]      # [{label, url, source}]


class PlannerState(TypedDict):
    # ── Inputs (set once at graph entry) ─────────────────────────────
    city: str
    user_goal: str             # Raw natural language from user
    budget: float              # USD
    preferences: list[str]     # Extracted tags: ["jazz", "street food", "outdoor"]
    date: str                  # ISO date for "today" queries, e.g. "2025-07-15"

    # ── Agent 1 output ────────────────────────────────────────────────
    events: list[Event]        # Scout → ranked, filtered event list

    # ── Agent 2 output ────────────────────────────────────────────────
    itinerary: list[ItineraryStop]   # Curator → conflict-free schedule

    # ── Agent 3 output ────────────────────────────────────────────────
    logistics: Optional[Logistics]   # Navigator → transit + budget check

    # ── Agent 4 output ────────────────────────────────────────────────
    plan: Optional[Plan]       # Narrator → final UI payload

    # ── Pipeline metadata ─────────────────────────────────────────────
    errors: list[str]          # Accumulated non-fatal errors from any agent
    started_at: str            # ISO timestamp for LangSmith tracing
