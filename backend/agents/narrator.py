# backend/agents/narrator.py
"""
Narrator Agent — Plan Presenter
READS:  state["itinerary"]   (from Curator)
        state["logistics"]   (from Navigator)
        state["events"]      (from Scout — for citations)
        state["budget"], state["user_goal"], state["preferences"]
WRITES: state["plan"]  (Plan TypedDict)

The Narrator is the final agent in the A2A chain. It reads the COMPLETE
enriched state and synthesises:
  1. A conversational day-plan summary with reasoning
  2. A merged timeline (itinerary stops + travel legs) for UI rendering
  3. Budget breakdown table
  4. Map pins for every venue
  5. Citations linking back to original data sources

TRUE A2A DEPENDENCY: Cannot function without Curator's itinerary AND
Navigator's logistics. This is the capstone proof of sequential A2A.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from .state import (
    PlannerState,
    Plan,
    ItineraryStop,
    Logistics,
)
from .llm import get_llm, ainvoke_with_fallback

logger = logging.getLogger(__name__)


# ── System prompt ─────────────────────────────────────────────────────────────

NARRATOR_SYSTEM = """\
You are The Narrator, the final agent in a city day-planner pipeline.
You receive a COMPLETE day plan — itinerary stops, travel logistics,
budget data, and the user's original goal — and you write a polished,
conversational summary.

## Your responsibilities
1. Write a 3–5 paragraph `summary` that reads like a friendly local guide
   narrating the user's upcoming day. Mention specific venue names, times,
   and why each stop was chosen. Reference the user's original goal/preferences.
2. Write a `reasoning` paragraph explaining any trade-offs: events that were
   dropped, budget constraints, or time conflicts the Curator resolved.
3. If the plan is over budget, acknowledge it explicitly and suggest what
   could be cut.

## Output Format
Return ONLY valid JSON — no markdown fences, no prose outside the JSON:

{
  "summary": "Your day in San Francisco starts at...",
  "reasoning": "We prioritised jazz venues because you mentioned...",
  "timeline": [
    {"time": "9:30 AM", "label": "Morning Jazz at Blue Note", "type": "event", "icon": "music"},
    {"time": "10:15 AM", "label": "Walk to Fisherman's Wharf (12 min)", "type": "travel", "icon": "walk"},
    ...
  ],
  "map_pins": [
    {"name": "Blue Note Jazz Club", "lat": 40.7308, "lng": -74.0006, "type": "event"},
    ...
  ],
  "budget_breakdown": {
    "events": 65.00,
    "transport": 18.50,
    "remaining": 36.50
  },
  "citations": [
    {"label": "Blue Note Jazz Club", "url": "https://...", "source": "ticketmaster"},
    ...
  ]
}

## Timeline rules
- Interleave event stops and travel legs chronologically.
- Event icons: "music", "food", "art", "sports", "festival", "museum", "coffee", "other"
- Travel icons: "walk", "transit", "rideshare"
- Every event from the itinerary MUST appear. Every travel leg MUST appear.
"""


# ── JSON extraction ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Robustly extract JSON from LLM response."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON in Narrator response: {text[:300]!r}")


# ── Build prompt context from full state ─────────────────────────────────────

def _build_narrator_context(
    itinerary: list[ItineraryStop],
    logistics: Logistics | None,
    budget: float,
    user_goal: str,
    preferences: list[str],
    events: list[dict],
) -> str:
    """Assemble the full enriched plan into a structured text block for the LLM."""

    lines = [
        "=" * 60,
        "COMPLETE DAY PLAN — READY FOR NARRATION",
        "=" * 60,
        "",
        f"USER GOAL: {user_goal}",
        f"PREFERENCES: {', '.join(preferences) if preferences else 'none specified'}",
        f"BUDGET: ${budget:.2f}",
        "",
        "─── ITINERARY (from Curator) ───",
    ]

    for i, stop in enumerate(itinerary, 1):
        lines.append(
            f"\n  Stop {i}: {stop['event_name']}"
            f"\n    Time:             {stop['time']}"
            f"\n    Venue:            {stop['venue']}"
            f"\n    Address:          {stop['address']}"
            f"\n    Cost:             ${stop['cost']:.2f}"
            f"\n    Budget Remaining: ${stop['budget_remaining']:.2f}"
            f"\n    Curator Notes:    {stop['notes']}"
        )

    if logistics:
        lines.append("\n─── TRAVEL LOGISTICS (from Navigator) ───")
        for i, leg in enumerate(logistics["legs"], 1):
            lines.append(
                f"\n  Leg {i}: {leg['from_venue']} → {leg['to_venue']}"
                f"\n    Mode:     {leg['mode']}"
                f"\n    Duration: {leg['duration_minutes']} min"
                f"\n    Cost:     ${leg['estimated_cost']:.2f}"
                f"\n    Route:    {leg['instructions']}"
            )

        lines.append(f"\n─── BUDGET SUMMARY ───")
        lines.append(f"  Total Event Cost:     ${logistics['total_event_cost']:.2f}")
        lines.append(f"  Total Transport Cost: ${logistics['total_transport_cost']:.2f}")
        lines.append(f"  Grand Total:          ${logistics['grand_total']:.2f}")
        lines.append(f"  Budget OK:            {'Yes' if logistics['budget_ok'] else 'NO — OVER BUDGET'}")
        if logistics.get("budget_warning"):
            lines.append(f"  Warning:              {logistics['budget_warning']}")
    else:
        lines.append("\n─── NO LOGISTICS DATA (Navigator may have failed) ───")

    # Include source URLs for citations
    lines.append("\n─── SOURCE URLS (for citations) ───")
    seen_urls = set()
    for stop in itinerary:
        # Find matching event for this stop's URL
        for ev in events:
            if ev["name"] == stop["event_name"] and ev.get("url") and ev["url"] not in seen_urls:
                lines.append(f"  {ev['name']} — {ev['source']} — {ev['url']}")
                seen_urls.add(ev["url"])
                break

    return "\n".join(lines)


# ── Build fallback plan from raw data (no LLM) ──────────────────────────────

def _build_fallback_plan(
    itinerary: list[ItineraryStop],
    logistics: Logistics | None,
    budget: float,
    events: list[dict],
) -> Plan:
    """Deterministic fallback if the LLM fails — assembles plan from raw data."""

    # Timeline: interleave stops and legs
    timeline: list[dict] = []
    for i, stop in enumerate(itinerary):
        timeline.append({
            "time": stop["time"],
            "label": f"{stop['event_name']} at {stop['venue']}",
            "type": "event",
            "icon": "other",
        })
        if logistics and i < len(logistics["legs"]):
            leg = logistics["legs"][i]
            timeline.append({
                "time": leg["departure_time"],
                "label": f"{leg['mode'].capitalize()} to {leg['to_venue']} ({leg['duration_minutes']} min)",
                "type": "travel",
                "icon": leg["mode"],
            })

    # Map pins
    map_pins = [
        {"name": stop["venue"], "lat": stop["lat"], "lng": stop["lng"], "type": "event"}
        for stop in itinerary
        if stop["lat"] != 0.0 and stop["lng"] != 0.0
    ]

    # Budget breakdown
    event_cost = logistics["total_event_cost"] if logistics else sum(s["cost"] for s in itinerary)
    transport_cost = logistics["total_transport_cost"] if logistics else 0.0
    budget_breakdown = {
        "events": event_cost,
        "transport": transport_cost,
        "remaining": round(budget - event_cost - transport_cost, 2),
    }

    # Citations
    citations = []
    seen = set()
    for stop in itinerary:
        for ev in events:
            if ev["name"] == stop["event_name"] and ev.get("url") and ev["url"] not in seen:
                citations.append({"label": ev["name"], "url": ev["url"], "source": ev["source"]})
                seen.add(ev["url"])
                break

    return Plan(
        summary=f"Your day includes {len(itinerary)} stops. Total estimated cost: ${event_cost + transport_cost:.2f}.",
        reasoning="Plan assembled from raw agent data (LLM narration unavailable).",
        timeline=timeline,
        map_pins=map_pins,
        budget_breakdown=budget_breakdown,
        citations=citations,
    )


# ── Validate LLM output ─────────────────────────────────────────────────────

def _validate_plan(data: dict) -> Plan:
    """Validate and coerce the LLM JSON into a Plan TypedDict."""
    for key in ("summary", "reasoning"):
        if key not in data or not isinstance(data[key], str):
            raise ValueError(f"Missing or invalid '{key}' in Narrator output")

    return Plan(
        summary=data["summary"],
        reasoning=data["reasoning"],
        timeline=data.get("timeline", []),
        map_pins=data.get("map_pins", []),
        budget_breakdown=data.get("budget_breakdown", {}),
        citations=data.get("citations", []),
    )


# ── Main Narrator node ───────────────────────────────────────────────────────

@traceable(name="narrator_node")
async def narrator_node(state: PlannerState) -> dict:
    """
    LangGraph node — FINAL agent in the A2A pipeline.
    Reads:  itinerary, logistics, events, budget, user_goal, preferences
    Writes: plan (Plan TypedDict)

    TRUE A2A DEPENDENCY: Requires both Curator AND Navigator output.
    """
    itinerary = state.get("itinerary", [])
    logistics = state.get("logistics")
    events = state.get("events", [])
    budget = state.get("budget", 100.0)
    user_goal = state.get("user_goal", "")
    preferences = state.get("preferences", [])
    errors = list(state.get("errors", []))

    # ── A2A gate: require upstream output ─────────────────────────────────
    if not itinerary:
        errors.append("Narrator: no itinerary — upstream agents may have failed")
        logger.warning("[Narrator] No itinerary in state")
        return {
            "plan": Plan(
                summary="Could not generate a day plan — no itinerary was available.",
                reasoning="The upstream agents (Scout/Curator) failed to produce an itinerary.",
                timeline=[],
                map_pins=[],
                budget_breakdown={},
                citations=[],
            ),
            "errors": errors,
        }

    # ── Build full context ────────────────────────────────────────────────
    context = _build_narrator_context(
        itinerary, logistics, budget, user_goal, preferences, events
    )

    user_message = (
        f"Write the final day-plan narration based on this complete plan data.\n\n"
        f"{context}\n\n"
        f"Generate the JSON with summary, reasoning, timeline, map_pins, "
        f"budget_breakdown, and citations. Be specific — name venues, times, "
        f"and costs. If over budget, say so and suggest what to cut."
    )

    # ── Call LLM (Gemini primary, Groq fallback on quota errors) ─────────────
    try:
        llm = get_llm(temperature=0.8)
        messages = [
            SystemMessage(content=NARRATOR_SYSTEM),
            HumanMessage(content=user_message),
        ]
        response = await ainvoke_with_fallback(llm, messages, temperature=0.8)
        data = _extract_json(response.content)
        plan = _validate_plan(data)

        logger.info(
            "[Narrator] Plan generated: %d timeline items, %d map pins, %d citations",
            len(plan["timeline"]),
            len(plan["map_pins"]),
            len(plan["citations"]),
        )

        return {"plan": plan, "errors": errors}

    except Exception as exc:
        errors.append(f"Narrator: LLM/parsing failed — {exc}")
        logger.error("[Narrator] Failed, using fallback: %s", exc, exc_info=True)

        # Deterministic fallback — still produces a usable plan
        fallback = _build_fallback_plan(itinerary, logistics, budget, events)
        return {"plan": fallback, "errors": errors}
