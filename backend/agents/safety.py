# backend/agents/safety.py
"""
Safety Agent — City Safety Briefing
Standalone agent (not part of the V2 pipeline).
Called by /api/city-safety in main.py.

Fetches recent safety/scam news via GNews, then uses the LLM to synthesise
4-5 actionable tips. Falls back to static LLM generation if GNews is unavailable.
"""

import json
import logging
import os
import re

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from .llm import get_llm, ainvoke_with_fallback

logger = logging.getLogger(__name__)

GNEWS_KEY  = os.getenv("GNEWS_API_KEY")
GNEWS_BASE = "https://gnews.io/api/v4"

SAFETY_SYSTEM = """\
You are a city safety advisor for international tourists.
Given a city name and optional recent news, generate 4–5 concise, actionable safety tips.

Focus on: pickpocket hotspots, transport scams, ticket fraud, unsafe neighbourhoods at night,
common tourist traps, and any city-specific risks.

Return ONLY a JSON array — no markdown fences, no prose outside the JSON:
[
  {
    "title": "Short tip title (5-8 words)",
    "description": "1-2 sentence, specific, actionable advice.",
    "level": "high",
    "icon": "🚨"
  }
]

level values: "high" (immediate physical/financial risk), "medium" (common nuisance/scam),
              "low" (minor awareness point).
icon: one relevant emoji per tip.
Mix levels — do not make everything "high".
"""


@traceable(name="safety_fetch_gnews")
async def _fetch_gnews_safety(city: str) -> list[dict]:
    """Pull recent safety/scam articles for the city from GNews."""
    if not GNEWS_KEY:
        return []
    params = {
        "q":      f"{city} tourist scam safety crime warning",
        "lang":   "en",
        "max":    "8",
        "apikey": GNEWS_KEY,
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{GNEWS_BASE}/search", params=params, timeout=8.0)
            r.raise_for_status()
            return r.json().get("articles", [])
    except Exception as exc:
        logger.warning("[Safety] GNews fetch failed: %s", exc)
        return []


def _validate_tip(t: dict) -> dict:
    level = t.get("level", "medium")
    if level not in ("high", "medium", "low"):
        level = "medium"
    return {
        "title":       str(t.get("title", "Safety Tip")),
        "description": str(t.get("description", "")),
        "level":       level,
        "icon":        str(t.get("icon", "⚠️")),
    }


def _static_fallback(city: str) -> list[dict]:
    return [
        {
            "title": "Watch for pickpockets in tourist areas",
            "description": f"Busy attractions and public transport in {city} attract professional pickpockets. Keep valuables in front pockets or a money belt.",
            "level": "high",
            "icon": "👜",
        },
        {
            "title": "Use only licensed taxis or ride apps",
            "description": "Avoid unmarked vehicles near tourist sites. Use metered black cabs or official ride-sharing apps to prevent fare scams.",
            "level": "medium",
            "icon": "🚕",
        },
        {
            "title": "Buy tickets only from official sources",
            "description": "Purchase event and attraction tickets from official venues or verified websites — counterfeit tickets near major sites are common.",
            "level": "medium",
            "icon": "🎟️",
        },
        {
            "title": "Stay on main streets after midnight",
            "description": "Stick to well-lit, busy streets after dark, especially when leaving entertainment districts.",
            "level": "low",
            "icon": "🌙",
        },
    ]


@traceable(name="safety_briefing")
async def fetch_safety_briefing(city: str) -> list[dict]:
    """
    Main entry point. Returns a list of validated safety tip dicts.
    """
    articles = await _fetch_gnews_safety(city)

    news_context = ""
    if articles:
        snippets = [
            f"- {a.get('title', '')}: {a.get('description', '')}"
            for a in articles[:6]
        ]
        news_context = "\n\nRecent news articles:\n" + "\n".join(snippets)

    prompt = (
        f"City: {city}{news_context}\n\n"
        f"Generate 4–5 safety tips for tourists visiting {city}."
    )

    try:
        llm = get_llm(temperature=0.3)
        response = await ainvoke_with_fallback(
            llm,
            [SystemMessage(content=SAFETY_SYSTEM), HumanMessage(content=prompt)],
            temperature=0.3,
        )
        text = response.content.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        raw = json.loads(match.group(0) if match else text)
        tips = [_validate_tip(t) for t in raw if isinstance(t, dict)]
        logger.info("[Safety] Generated %d tips for %s", len(tips), city)
        return tips or _static_fallback(city)
    except Exception as exc:
        logger.error("[Safety] LLM failed: %s", exc)
        return _static_fallback(city)
