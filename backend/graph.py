"""
graph.py — LangGraph StateGraph orchestrating the 4 GeoLens agents.

Architecture:
  START → orchestrator_node (asyncio.gather) → END

All 4 agents run truly in parallel inside orchestrator_node via asyncio.gather,
which fires all 4 Ollama requests concurrently. LangGraph manages state flow.
"""

import asyncio
import logging
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agents import run_factoid, run_gastro, run_ledger, run_newshound

logger = logging.getLogger(__name__)


# ─── Shared State ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    city: str
    time_state: str
    newshound: Optional[dict]
    gastroGuide: Optional[dict]
    factoid: Optional[dict]
    ledger: Optional[dict]


# ─── Orchestrator Node ────────────────────────────────────────────────────────

async def orchestrator_node(state: AgentState) -> dict:
    """
    Run all 4 agents concurrently via asyncio.gather.
    Returns a partial state update with all 4 agent outputs.
    """
    city = state["city"]
    time_state = state["time_state"]
    logger.info("⚡ Running 4 agents in parallel for city=%r time_state=%r", city, time_state)

    newshound, gastro, factoid, ledger = await asyncio.gather(
        run_newshound(city, time_state),
        run_gastro(city, time_state),
        run_factoid(city, time_state),
        run_ledger(city, time_state),
    )

    logger.info("✓ All 4 agents completed for %r", city)
    return {
        "newshound": newshound,
        "gastroGuide": gastro,
        "factoid": factoid,
        "ledger": ledger,
    }


# ─── Graph Construction ───────────────────────────────────────────────────────

def _build_graph() -> object:
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_edge(START, "orchestrator")
    builder.add_edge("orchestrator", END)
    return builder.compile()


_graph = _build_graph()


# ─── Public Entrypoint ────────────────────────────────────────────────────────

async def run_graph(city: str, time_state: str) -> dict:
    """
    Invoke the compiled graph and return the final agent data dict.
    Response keys match the AgentData TypeScript type in geolens-app/lib/types.ts.
    """
    initial_state: AgentState = {
        "city": city,
        "time_state": time_state,
        "newshound": None,
        "gastroGuide": None,
        "factoid": None,
        "ledger": None,
    }
    result: AgentState = await _graph.ainvoke(initial_state)  # type: ignore[arg-type]
    return {
        "newshound": result["newshound"],
        "gastroGuide": result["gastroGuide"],
        "factoid": result["factoid"],
        "ledger": result["ledger"],
    }
