"""
agents.py — The 4 GeoLens intelligence agents.

Each async function calls the local Ollama LLM (gemma4:e4b) with a strict
JSON-only system prompt. The agents execute the "Fetch-and-Inject" pattern
where Python grabs real internet/CSV data first, injects it into the prompt,
and the LLM formats it.
"""

import json
import logging
import random
import re
import os
import csv
import asyncio
import time
import requests
import wikipedia

_EXCHANGE_CACHE = {}

def get_usd_exchange_rate(local_currency: str) -> float:
    """Fetch live USD exchange rate, with a 60-minute cache."""
    now = time.time()
    if local_currency in _EXCHANGE_CACHE:
        cached_rate, timestamp = _EXCHANGE_CACHE[local_currency]
        if now - timestamp < 3600:
            return cached_rate

    try:
        url = f"https://open.er-api.com/v6/latest/{local_currency}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            rate = data.get('rates', {}).get('USD', 1.0)
            _EXCHANGE_CACHE[local_currency] = (rate, now)
            return rate
    except Exception as e:
        logger.error(f"Exchange API error: {e}")
        
    return 1.0
from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "llama-3.1-8b-instant"


def _get_llm(temperature: float = 0.7) -> ChatGroq:
    """Return a ChatGroq instance for blazing fast inference."""
    return ChatGroq(model=MODEL, temperature=temperature)


def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from an LLM response.
    Handles: raw JSON, markdown code fences, mixed prose + JSON.
    """
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

    raise ValueError(f"No valid JSON in LLM response: {text[:300]!r}")


def _random_time_ago() -> str:
    return random.choice(["8 min ago", "15 min ago", "22 min ago", "34 min ago", "1 hr ago", "2 hr ago"])


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 · The Newshound
# ─────────────────────────────────────────────────────────────────────────────

_NEWSHOUND_SYSTEM = """\
You are The Newshound, a hyper-local news agent embedded in a city intelligence app.
Select exactly 3 realistic, specific current-sounding news items for the city provided.

Return ONLY a raw JSON object containing an "items" array — no markdown, no code fences, no explanation.
Each item in the array must have the following required keys (all strings):
  headline  — punchy headline, max 12 words
  summary   — exactly 2 vivid, specific sentences
  tag       — ONE of: "Local Event" | "Arts & Culture" | "Technology" | "Sports" | "Society & Events" | "Politics" | "Environment" | "Business"
  timeAgo   — ONE of: "8 min ago" | "15 min ago" | "22 min ago" | "34 min ago" | "1 hr ago" | "2 hr ago" | "3 hr ago"
  url       — the actual URL to the original article provided in the live context

Example output:
{"items": [{"headline":"Shibuya...","summary":"...","tag":"Local Event","timeAgo":"34 min ago","url":"..."}, {"headline":"...","summary":"...","tag":"...","timeAgo":"...","url":"..."}, {"headline":"...","summary":"...","tag":"...","timeAgo":"...","url":"..."}]}
"""

async def run_newshound(city: str, time_state: str) -> dict:
    """The Newshound."""
    fallback = {"error": True, "items": []}
    try:
        def fetch_news():
            api_key = os.getenv("GNEWS_API_KEY")
            if not api_key:
                return []
            url = f"https://gnews.io/api/v4/search?q={city} local&lang=en&max=3&apikey={api_key}"
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    return resp.json().get("articles", [])
            except Exception as e:
                logger.error(f"GNews API error: {e}")
            return []
            
        loop = asyncio.get_event_loop()
        news_items = await loop.run_in_executor(None, fetch_news)
        
        live_context = "LIVE NEWS DATA:\n"
        if news_items:
            for item in news_items:
                live_context += f"- Title: {item.get('title')}\n  Published: {item.get('publishedAt')}\n  URL: {item.get('url')}\n"
        else:
            live_context += "No recent news found.\n"

        llm = _get_llm(temperature=0.75)
        messages = [
            SystemMessage(content=_NEWSHOUND_SYSTEM),
            HumanMessage(content=f"City: {city}\nTime of day: {time_state}\n\n{live_context}\n\nSelect 3 distinct real stories from the LIVE NEWS DATA (or make plausible ones up if none are found) and format them into the required JSON Schema:")
        ]
        response = await llm.ainvoke(messages)
        data = _extract_json(response.content)
        if "items" not in data or not isinstance(data["items"], list) or not data["items"]:
            raise ValueError("Missing 'items' array")
        for item in data["items"]:
            for key in ("headline", "summary", "tag", "timeAgo"):
                if key not in item:
                    raise ValueError(f"Missing required key: {key!r}")
        return data
    except Exception as exc:
        logger.warning("[Newshound] Returning fallback — %s", exc)
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2 · The Gastro-Guide
# ─────────────────────────────────────────────────────────────────────────────

_GASTRO_SYSTEM = """\
You are The Gastro-Guide, a world-class food intelligence agent for a city app.
Recommend exactly 3 iconic local dishes and specific, real-sounding restaurants in the given city.
The insider tips should be practical, opinionated, and specific — advice from a seasoned food critic who lives there.

Return ONLY a raw JSON object containing an "items" array — no markdown, no code fences, no explanation.
Each item in the array must have the following required keys (all strings):
  dish        — dish name; optionally add a sub-description after a · separator
  restaurant  — "Restaurant Name, Neighbourhood" format
  tip         — 2–3 sentences of specific, opinionated insider advice
  emoji       — a single food emoji that best represents the dish
  url         — the generated Google Maps URL to view the restaurant location

Example output:
{"items": [{"dish":"Tsukemen · Dipping Ramen","restaurant":"Fuunji, Shinjuku","tip":"Arrive before 11AM...","emoji":"🍜","url":"..."}, {"dish":"...","restaurant":"...","tip":"...","emoji":"...","url":"..."}, {"dish":"...","restaurant":"...","tip":"...","emoji":"...","url":"..."}]}
"""

async def run_gastro(city: str, time_state: str) -> dict:
    """The Gastro-Guide."""
    fallback = {"error": True, "items": []}
    try:
        def fetch_foursquare():
            api_key = os.getenv("FOURSQUARE_API_KEY")
            if not api_key:
                return "No Foursquare API key provided."
            
            query = "cafe" if time_state == "day" else "restaurant"
            url = f"https://api.foursquare.com/v3/places/search?near={city}&query={query}&limit=3"
            headers = {"Authorization": api_key, "accept": "application/json"}
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    context = "LIVE RESTAURANT DATA:\n"
                    for r in results:
                        categories = r.get("categories", [{}])
                        cat_title = categories[0].get("name", "Food") if categories else "Food"
                        rating = r.get("rating", "N/A")
                        import urllib.parse
                        res_name = r.get('name')
                        query_enc = urllib.parse.quote_plus(f"{city} {res_name}")
                        maps_url = f"https://www.google.com/maps/search/?api=1&query={query_enc}"
                        context += f"- {res_name} ({cat_title}): Rating {rating}\n  URL: {maps_url}\n"
                    return context
                return f"Could not fetch data via Foursquare API: {resp.status_code}"
            except Exception as e:
                return f"Foursquare API error: {e}"

        loop = asyncio.get_event_loop()
        live_context = await loop.run_in_executor(None, fetch_foursquare)

        llm = _get_llm(temperature=0.85)
        messages = [
            SystemMessage(content=_GASTRO_SYSTEM),
            HumanMessage(content=f"City: {city}\nTime of day context: {time_state}\n\n{live_context}\n\nUse the LIVE RESTAURANT DATA to pick 3 acclaimed places and dishes. Generate the gastro JSON:")
        ]
        response = await llm.ainvoke(messages)
        data = _extract_json(response.content)
        if "items" not in data or not isinstance(data["items"], list) or not data["items"]:
            raise ValueError("Missing 'items' array")
        for item in data["items"]:
            for key in ("dish", "restaurant", "tip", "emoji"):
                if key not in item:
                    raise ValueError(f"Missing required key: {key!r}")
        return data
    except Exception as exc:
        logger.warning("[Gastro] Returning fallback — %s", exc)
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3 · The Factoid
# ─────────────────────────────────────────────────────────────────────────────

_FACTOID_SYSTEM = """\
You are The Factoid, a historical and cultural intelligence agent for a city app.
Surface exactly 3 surprising, little-known, genuinely fascinating historical or cultural facts about the given city.

Return ONLY a raw JSON object containing an "items" array — no markdown, no code fences, no explanation.
Each item in the array must have the following required keys (both strings):
  fact    — 2–3 sentences. Surprising, grounded, specific, strictly based on provided text.
  source  — a plausible real institution, museum, archive, or publication
  url     — the Wikipedia URL provided in the live context

Example output:
{"items": [{"fact":"Tokyo was called Edo until 1868.","source":"Edo-Tokyo Museum","url":"..."}, {"fact":"...","source":"...","url":"..."}, {"fact":"...","source":"...","url":"..."}]}
"""

async def run_factoid(city: str, time_state: str) -> dict: 
    """The Factoid."""
    fallback = {"error": True, "items": []}
    try:
        def fetch_wiki():
            try:
                page = wikipedia.page(f"{city} history culture")
                return f"URL: {page.url}\n\n{page.summary[:1500]}"
            except Exception:
                page = wikipedia.page(city)
                return f"URL: {page.url}\n\n{page.summary[:1500]}"

        loop = asyncio.get_event_loop()
        live_context = await loop.run_in_executor(None, fetch_wiki)
        
        llm = _get_llm(temperature=0.9)
        messages = [
            SystemMessage(content=_FACTOID_SYSTEM),
            HumanMessage(content=f"City: {city}\n\nLIVE WIKIPEDIA DATA:\n{live_context}\n\nFind 3 of the most surprising historical or cultural facts in this text and generate the factoid JSON:")
        ]
        response = await llm.ainvoke(messages)
        data = _extract_json(response.content)
        if "items" not in data or not isinstance(data["items"], list) or not data["items"]:
            raise ValueError("Missing 'items' array")
        for item in data["items"]:
            for key in ("fact", "source"):
                if key not in item:
                    raise ValueError(f"Missing required key: {key!r}")
        return data
    except Exception as exc:
        logger.warning("[Factoid] Returning fallback — %s", exc)
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4 · The Local Ledger
# ─────────────────────────────────────────────────────────────────────────────

_LEDGER_SYSTEM = """\
You are The Local Ledger, a cost-of-living intelligence agent for a city app.
Generate realistic typical prices for 3 daytime expenses and 3 nighttime expenses in the given city.
Use the actual local currency. Format data precisely.

Return ONLY a raw JSON object — no markdown, no code fences, no explanation.
Use exactly 3 items per array (cheap, medium, expensive).

{
  "day": [
    {"label": "item name", "icon": "single emoji", "priceLocal": "local price with symbol", "priceUSD": "$X.XX", "level": "cheap"},
    {"label": "item name", "icon": "single emoji", "priceLocal": "local price with symbol", "priceUSD": "$X.XX", "level": "medium"},
    {"label": "item name", "icon": "single emoji", "priceLocal": "local price with symbol", "priceUSD": "$X.XX", "level": "expensive"}
  ],
  "night": [ ... same structure ... ]
}
"""

_LEDGER_FALLBACK: dict = {
    "error": True,
    "day": [],
    "night": [],
}

_VALID_LEVELS = frozenset({"cheap", "medium", "expensive"})

def _validate_and_coerce_ledger(data: dict) -> dict:
    if "day" not in data or "night" not in data:
        raise ValueError("Missing 'day' or 'night'")
    for section in ("day", "night"):
        if not isinstance(data[section], list) or len(data[section]) < 1:
            raise ValueError(f"'{section}' must be a list")
        for item in data[section]:
            if item.get("level") not in _VALID_LEVELS:
                item["level"] = "medium"
    return data

async def run_ledger(city: str, time_state: str) -> dict: 
    """The Local Ledger."""
    city_lower = city.lower()
    if "tokyo" in city_lower:
        local_currency = "JPY"
    elif "new york" in city_lower:
        local_currency = "USD"
    elif "london" in city_lower:
        local_currency = "GBP"
    elif "paris" in city_lower:
        local_currency = "EUR"
    elif "sydney" in city_lower:
        local_currency = "AUD"
    else:
        local_currency = "USD"

    try:
        def fetch_csv():
            csv_path = os.path.join(os.path.dirname(__file__), "..", "data", "cost_of_living.csv")
            try:
                with open(csv_path, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if city.lower() in row.get("city", "").lower():
                            return f"LIVE COST DATA FOR {city}: Coffee={row.get('coffee_price')} {row.get('currency_symbol')}, Transit={row.get('transit_price')} {row.get('currency_symbol')}, Museum={row.get('museum_price')} {row.get('currency_symbol')}, Beer={row.get('beer_price')} {row.get('currency_symbol')}, Rideshare={row.get('rideshare_price')} {row.get('currency_symbol')}, Club={row.get('club_price')} {row.get('currency_symbol')}"
                    return f"No specific data found for {city}. Use plausible estimates."
            except FileNotFoundError:
                return "Cost of living CSV file missing."
                
        loop = asyncio.get_event_loop()
        live_context = await loop.run_in_executor(None, fetch_csv)
        live_rate = await loop.run_in_executor(None, get_usd_exchange_rate, local_currency)

        llm = _get_llm(temperature=0.5)
        messages = [
            SystemMessage(content=_LEDGER_SYSTEM),
            HumanMessage(content=f"City: {city}\n\n{live_context}\n\nThe live exchange rate is 1 {local_currency} = {live_rate:.4f} USD. Use this exact rate to accurately calculate and display the USD equivalent for the items.\nFormat these raw numbers using the strict JSON day/night array schema:")
        ]
        response = await llm.ainvoke(messages)
        data = _extract_json(response.content)
        return _validate_and_coerce_ledger(data)
    except Exception as exc:
        logger.warning("[Ledger] Returning fallback — %s", exc)
        return _LEDGER_FALLBACK
