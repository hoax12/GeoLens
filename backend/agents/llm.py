"""
llm.py — Centralised LLM factory for the V2 Day Planner pipeline.

All V2 agents import their LLM from here. Swapping the model is a one-line
change in this file — nothing else in the pipeline needs to touch.

Strategy:
  Primary:  Gemini 2.5 Flash (Google Generative AI)
  Fallback: Llama-3.3-70b via Groq (kicks in on 429 / quota errors)

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

GEMINI_MODEL = "gemini-2.5-flash"
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"


# ── Factories ─────────────────────────────────────────────────────────────────

def get_gemini(temperature: float = 0.7) -> ChatGoogleGenerativeAI:
    """Return a Gemini 2.5 Flash instance (primary LLM)."""
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        temperature=temperature,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
    )


def get_groq(temperature: float = 0.7) -> ChatGroq:
    """Return a Groq LLaMA instance (fallback LLM)."""
    return ChatGroq(
        model=GROQ_FALLBACK_MODEL,
        temperature=temperature,
        api_key=os.getenv("GROQ_API_KEY"),
    )


def get_llm(temperature: float = 0.7) -> BaseChatModel:
    """
    Return the best available LLM.

    Tries Gemini first. If the GOOGLE_API_KEY is missing or the daily
    free-tier quota is exhausted (429), falls back to Groq automatically.
    All V2 agents should call this instead of get_gemini() directly.
    """
    google_key = os.getenv("GOOGLE_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if google_key:
        return get_gemini(temperature)
    elif groq_key:
        logger.warning("[LLM] No GOOGLE_API_KEY — using Groq fallback (%s)", GROQ_FALLBACK_MODEL)
        return get_groq(temperature)
    else:
        raise RuntimeError("No LLM API key configured. Set GOOGLE_API_KEY or GROQ_API_KEY in .env")


async def ainvoke_with_fallback(llm: BaseChatModel, messages: list, temperature: float = 0.7) -> object:
    """
    Invoke the given LLM. On a 429 quota error, transparently retry with Groq.

    Usage:
        llm = get_llm()
        response = await ainvoke_with_fallback(llm, messages)
    """
    try:
        return await llm.ainvoke(messages)
    except Exception as exc:
        err_str = str(exc)
        is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower()

        if is_quota and os.getenv("GROQ_API_KEY"):
            logger.warning(
                "[LLM] Gemini quota exceeded — falling back to Groq (%s). Error: %s",
                GROQ_FALLBACK_MODEL,
                err_str[:120],
            )
            groq_llm = get_groq(temperature)
            return await groq_llm.ainvoke(messages)

        # Re-raise for non-quota errors or if no fallback key exists
        raise
