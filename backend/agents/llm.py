"""
llm.py — Centralised LLM factory for the V2 Day Planner pipeline.

All V2 agents import their LLM from here. Swapping the model is a one-line
change in this file — nothing else in the pipeline needs to touch.

Strategy:
  Primary:  Groq Llama-3.3-70b (1-3s per call, generous free tier)
  Fallback: Gemini 2.0 Flash (kicks in on Groq 429 / quota errors)

V1 agents (v1_agents.py) still use ChatGroq directly — this file does NOT affect them.
"""

import logging
import os

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

load_dotenv()
logger = logging.getLogger(__name__)

# ── Model names ───────────────────────────────────────────────────────────────

GROQ_PRIMARY_MODEL  = "llama-3.3-70b-versatile"
GEMINI_FALLBACK_MODEL = "gemini-2.0-flash"


# ── Factories ─────────────────────────────────────────────────────────────────

def get_groq(temperature: float = 0.7) -> ChatGroq:
    """Return a Groq LLaMA instance (primary LLM — fast, ~1-3s)."""
    return ChatGroq(
        model=GROQ_PRIMARY_MODEL,
        temperature=temperature,
        api_key=os.getenv("GROQ_API_KEY"),
    )


def get_gemini(temperature: float = 0.7) -> ChatGoogleGenerativeAI:
    """Return a Gemini 2.0 Flash instance (fallback LLM)."""
    return ChatGoogleGenerativeAI(
        model=GEMINI_FALLBACK_MODEL,
        temperature=temperature,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
    )


def get_llm(temperature: float = 0.7) -> BaseChatModel:
    """
    Return the best available LLM.

    Tries Groq first (fast). If GROQ_API_KEY is missing or quota is
    exhausted (429), falls back to Gemini 2.0 Flash automatically.
    All V2 agents should call this instead of instantiating models directly.
    """
    groq_key   = os.getenv("GROQ_API_KEY")
    google_key = os.getenv("GOOGLE_API_KEY")

    if groq_key:
        return get_groq(temperature)
    elif google_key:
        logger.warning("[LLM] No GROQ_API_KEY — using Gemini fallback (%s)", GEMINI_FALLBACK_MODEL)
        return get_gemini(temperature)
    else:
        raise RuntimeError("No LLM API key configured. Set GROQ_API_KEY or GOOGLE_API_KEY in .env")


async def ainvoke_with_fallback(llm: BaseChatModel, messages: list, temperature: float = 0.7) -> object:
    """
    Invoke the given LLM. On a 429 quota error, transparently retry with Gemini.

    Usage:
        llm = get_llm()
        response = await ainvoke_with_fallback(llm, messages)
    """
    try:
        return await llm.ainvoke(messages)
    except Exception as exc:
        err_str = str(exc)
        is_quota = "429" in err_str or "rate_limit" in err_str.lower() or "quota" in err_str.lower()

        if is_quota and os.getenv("GOOGLE_API_KEY"):
            logger.warning(
                "[LLM] Groq quota exceeded — falling back to Gemini (%s). Error: %s",
                GEMINI_FALLBACK_MODEL,
                err_str[:120],
            )
            gemini_llm = get_gemini(temperature)
            return await gemini_llm.ainvoke(messages)

        raise
