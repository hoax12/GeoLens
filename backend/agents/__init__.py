"""
GeoLens V2 Agents Package — City Day Planner Pipeline.

Sequential A2A pipeline: Scout → Curator → Navigator → Narrator.
Each agent reads from and writes to the shared PlannerState.
"""

from .state import PlannerState, Event, ItineraryStop, TravelLeg, Logistics, Plan
from .scout import scout_node
from .curator import curator_node
from .navigator import navigator_node
from .narrator import narrator_node

__all__ = [
    "PlannerState",
    "Event",
    "ItineraryStop",
    "TravelLeg",
    "Logistics",
    "Plan",
    "scout_node",
    "curator_node",
    "navigator_node",
    "narrator_node",
]
