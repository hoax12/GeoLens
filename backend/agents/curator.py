# backend/agents/curator.py
"""
Curator Agent — Itinerary Builder
READS:  state["events"]  (from Scout)
        state["budget"]
        state["preferences"]
WRITES: state["itinerary"]  (list[ItineraryStop])

The Curator is the first LLM-reasoning agent in the pipeline. It cannot
function without Scout's event list — this is the A2A dependency proof.

The LLM receives the full ranked event list and must:
  1. Select non-overlapping events that fit within the day
  2. Allocate budget across events + food buffer + transport reserve
  3. Explain choices in each stop's `notes` field (reasoning transparency)
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from .state import PlannerState, ItineraryStop, Event
from .llm import get_llm, ainvoke_with_fallback

logger = logging.getLogger(__name__)


# ── System prompt ─────────────────────────────────────────────────────────────

CURATOR_SYSTEM = """\
You are The Curator, an expert day-planner agent inside a city intelligence app.

You receive a ranked list of events/venues discovered by a Scout agent.
Your job is to build a realistic, conflict-free day itinerary from these events.

## Rules
1. Select 3–5 stops that fit within a single day (roughly 9 AM to 11 PM).
2. Space stops at least 45 minutes apart to allow travel time.
3. Never schedule overlapping events.
4. The total cost of all selected stops MUST stay within the user's budget.
   - Reserve ~15% of budget for transport between stops.
   - Reserve ~20% of budget for meals/snacks if no food stops are included.
5. Prefer events with higher relevance_score (the Scout already ranked them).
6. If two events conflict in time, pick the one with higher relevance_score.
7. For each stop, write a ONE-SENTENCE `notes` field explaining the key reason you chose it.
8. For each stop, add a `peak_warning` field: a ONE-SENTENCE crowd/timing warning if the
   scheduled time coincides with a known busy period for that venue type. Examples:
   - Museums/galleries: busy 10 AM–2 PM on weekends
   - Restaurants: busy 12–1 PM (lunch) and 7–9 PM (dinner)
   - Markets: busy Saturday mornings
   - Concert venues: busy 30 min before show time
   Leave as "" if no warning applies or the slot is already off-peak.

## Output Format
Return ONLY a valid JSON object with the following structure — no markdown
fences, no explanation, no prose outside the JSON:

{
  "itinerary": [
    {
      "time": "9:30 AM",
      "event_name": "Morning Jazz at Blue Note",
      "venue": "Blue Note Jazz Club",
      "address": "131 W 3rd St, New York",
      "lat": 40.7308,
      "lng": -74.0006,
      "cost": 25.00,
      "budget_remaining": 95.00,
      "notes": "Chosen for jazz preference match (score 0.95). Morning slot avoids crowd.",
      "peak_warning": ""
    }
  ]
}

The `budget_remaining` field must DECREASE with each stop and NEVER go below 0.
The itinerary must be sorted chronologically by `time`.
"""


# ── JSON extraction (same robust pattern from V1) ────────────────────────────

def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from an LLM response.
    Handles: raw JSON, markdown code fences, mixed prose + JSON.
    """
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try code fence extraction
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding any JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON in Curator LLM response: {text[:300]!r}")


# ── Format events for prompt context ─────────────────────────────────────────

def _format_events_for_prompt(events: list[Event], budget: float) -> str:
    """Build a structured text block from Scout's event list for the LLM."""
    if not events:
        return "NO EVENTS AVAILABLE — the Scout found nothing. Return an empty itinerary."

    lines = [
        f"USER BUDGET: ${budget:.2f}",
        f"TOTAL EVENTS DISCOVERED: {len(events)}",
        "",
        "RANKED EVENT LIST (highest relevance first):",
        "=" * 60,
    ]

    for i, ev in enumerate(events, 1):
        lines.append(
            f"\n[{i}] {ev['name']}"
            f"\n    Time:      {ev['time']}"
            f"\n    Venue:     {ev['location']}"
            f"\n    Address:   {ev['address']}"
            f"\n    Coords:    ({ev['lat']:.4f}, {ev['lng']:.4f})"
            f"\n    Cost:      ${ev['cost']:.2f}"
            f"\n    Category:  {ev['category']}"
            f"\n    Source:    {ev['source']}"
            f"\n    Relevance: {ev['relevance_score']:.3f}"
        )

    return "\n".join(lines)


# ── Validate & coerce LLM output ─────────────────────────────────────────────

def _validate_itinerary(data: dict, budget: float) -> list[ItineraryStop]:
    """
    Validate the LLM's JSON output and coerce into ItineraryStop list.
    Raises ValueError if the output is malformed.
    """
    if "itinerary" not in data:
        raise ValueError("Missing 'itinerary' key in Curator output")

    raw_stops = data["itinerary"]
    if not isinstance(raw_stops, list) or not raw_stops:
        raise ValueError("'itinerary' must be a non-empty list")

    stops: list[ItineraryStop] = []
    running_budget = budget

    for i, stop in enumerate(raw_stops):
        # Required fields
        for key in ("time", "event_name", "venue"):
            if key not in stop:
                raise ValueError(f"Stop {i} missing required key: {key!r}")

        cost = float(stop.get("cost", 0.0))
        running_budget -= cost

        stops.append(ItineraryStop(
            time=str(stop["time"]),
            event_name=str(stop["event_name"]),
            venue=str(stop["venue"]),
            address=str(stop.get("address", "")),
            lat=float(stop.get("lat", 0.0)),
            lng=float(stop.get("lng", 0.0)),
            cost=cost,
            budget_remaining=round(max(running_budget, 0.0), 2),
            notes=str(stop.get("notes", "")),
            peak_warning=str(stop.get("peak_warning", "")),
        ))

    return stops


# ── LLM stats extraction ─────────────────────────────────────────────────────

def _llm_stats(response) -> tuple[int, str]:
    """Extract (total_tokens, model_name) from a LangChain AIMessage."""
    tokens = int((getattr(response, 'usage_metadata', None) or {}).get('total_tokens', 0) or 0)
    model  = str((getattr(response, 'response_metadata', None) or {}).get('model_name', 'unknown') or 'unknown')
    return tokens, model


# ── Main Curator node ─────────────────────────────────────────────────────────

@traceable(name="curator_node")
async def curator_node(state: PlannerState) -> dict:
    """
    LangGraph node.
    Reads:  events, budget, preferences
    Writes: itinerary (list[ItineraryStop])

    TRUE A2A DEPENDENCY: If state["events"] is empty or missing,
    the Curator cannot build an itinerary — it returns an error.
    """
    start  = time.monotonic()
    events = state.get("events", [])
    budget = state.get("budget", 100.0)
    prefs  = state.get("preferences", [])
    errors = list(state.get("errors", []))
    run_stats = list(state.get("run_stats", []))

    # ── A2A gate: require Scout output ────────────────────────────────────
    if not events:
        errors.append("Curator: no events from Scout — cannot build itinerary")
        logger.warning("[Curator] No events in state — Scout may have failed")
        run_stats.append({"agent": "curator", "tokens_used": 0, "latency_ms": int((time.monotonic() - start) * 1000), "model_used": "none"})
        return {"itinerary": [], "errors": errors, "run_stats": run_stats}

    # ── Build prompt context ──────────────────────────────────────────────
    event_context = _format_events_for_prompt(events, budget)

    local_mode = any(p.lower().replace(" ", "_") == "local_mode" for p in prefs)
    display_prefs = [p for p in prefs if p.lower().replace(" ", "_") != "local_mode"]
    pref_str = ", ".join(display_prefs) if display_prefs else "no specific preferences"

    local_instruction = ""
    if local_mode:
        local_instruction = (
            "\nLOCAL MODE ACTIVE: The user wants to experience the city like a resident, not a tourist. "
            "STRONGLY PREFER: independent/family-owned venues, neighborhood spots away from tourist zones, "
            "local markets, community events, and places frequented by residents. "
            "ACTIVELY AVOID: chain restaurants, hotel bars, venues in major tourist zones, "
            "and attractions that dominate generic guidebooks.\n"
        )

    user_message = (
        f"Build a day itinerary for the following city events.\n"
        f"User preferences: {pref_str}\n"
        f"Budget: ${budget:.2f}\n"
        f"{local_instruction}"
        f"\n{event_context}\n\n"
        f"Select the best 4-7 stops, schedule them chronologically, "
        f"track the budget, and explain each choice in the notes field. "
        f"Return the JSON itinerary now:"
    )

    # ── Call LLM (Gemini primary, Groq fallback on quota errors) ─────────────
    try:
        llm = get_llm(temperature=0.6)
        messages = [
            SystemMessage(content=CURATOR_SYSTEM),
            HumanMessage(content=user_message),
        ]
        response = await ainvoke_with_fallback(llm, messages, temperature=0.6)
        tokens_used, model_used = _llm_stats(response)
        data = _extract_json(response.content)
        itinerary = _validate_itinerary(data, budget)

        logger.info(
            "[Curator] Built itinerary with %d stops, final budget: $%.2f",
            len(itinerary),
            itinerary[-1]["budget_remaining"] if itinerary else budget,
        )

        run_stats.append({"agent": "curator", "tokens_used": tokens_used, "latency_ms": int((time.monotonic() - start) * 1000), "model_used": model_used})
        return {"itinerary": itinerary, "errors": errors, "run_stats": run_stats}

    except Exception as exc:
        errors.append(f"Curator: LLM/parsing failed — {exc}")
        logger.error("[Curator] Failed: %s", exc, exc_info=True)
        run_stats.append({"agent": "curator", "tokens_used": 0, "latency_ms": int((time.monotonic() - start) * 1000), "model_used": "error"})
        return {"itinerary": [], "errors": errors, "run_stats": run_stats}
