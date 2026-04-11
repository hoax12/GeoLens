# GeoLens Progress Report

This document outlines the development progress, architecture, and current state of the GeoLens application. It is intended to serve as a comprehensive summary to assist with planning the next set of features.

## 1. Project Overview & Architecture

GeoLens is an interactive globe application powered by a multi-agent backend architecture. The application is bifurcated into a Next.js frontend and a Python/FastAPI backend, operating together to fetch missing data dynamically and display insights for selected global cities.

### 1.1 Backend Stack (Python / FastAPI)
* **API Framework**: Built on FastAPI (`main.py`) routing requests for `/api/city-info`.
* **Agent Orchestration**: `langgraph` is used to run multiple parallel data gathering agents asynchronously. 
* **LLM Engine**: Uses `ChatGroq` (`llama-3.1-8b-instant`) for fast inference to parse and format data cleanly into JSON.
* **Integrations**:
  * **Newshound**: Queries GNews API for localized recent news data.
  * **Gastro-Guide**: Connects to Foursquare API to suggest restaurants and cafes.
  * **Factoid**: Uses Wikipedia to surface historical and cultural contexts.
  * **Local Ledger**: Custom cost-of-living CSV combined with live currency exchange rates (`open.er-api.com`).

### 1.2 Frontend Stack (Next.js / React)
* **Framework**: Next.js 15+ (App Router).
* **Styling**: TailwindCSS & Vercel Geist Font. 
* **Core Components**:
  * `GlobeCanvas.tsx`: Interactive 3D globe visualization.
  * `AgentSidebar.tsx`: The primary dashboard hosting widget cards for different agents.
  * `AgentCards/*`: Specialized UI cards (e.g., `FactoidCard.tsx`, Newshound, Gastro-Guide, Ledger).

---

## 2. Completed Features and Milestones

### M1: Multi-Agent LLM Backend Setup
* Orchestrated a 4-agent parallel pipeline in LangGraph to generate local city intelligence.
* Established the "Fetch-and-Inject" pattern where standard Python queries live APIs (or CSV) first and injects factual info as context for the LLM to format (ensuring grounding and reducing hallucination).
* Standardized robust JSON extraction mechanisms from the Groq LLM responses to ensure structured parsing.

### M2: Dynamic Citations & Source Linking
* Enhanced the underlying agents to provide verifiable source URLs tied to their generated insights.
* Updated the frontend UI to display these as interactive **"View More" links**, granting users direct access directly to Google Maps, Wikipedia, GNews sources.

### M3: Honest Service Fallbacks (Progressive Disclosure UI)
* Replaced hardcoded "Mock Data" fallbacks with transparent error handling.
* The frontend now detects API/Agent failures dynamically.
* Implemented a clean, centered **"Service currently offline" state** utilizing the Lucide `CloudOff` icon to communicate gracefully whenever an agent is unreachable.

### M4: External API Integrations Setup
* Successfully wired APIs for live querying:
  * **Foursquare API**: Validated for extracting localized restaurants.
  * **GNews API**: Linked for realtime local stories.
  * **Fireworks AI / Local Ollama (gemma4)** tests ran before moving to Groq's high-speed endpoint.

---

## 3. Current System State

* **System is Functional**: The system spins up an interactive UI running on port `3000` and requests against `localhost:8000/api/city-info` to pull in the LLM-processed multi-card layouts.
* **Resilience**: A major emphasis was placed on ensuring UI error-boundaries. If API keys limit out or data isn't found, the cards will show a clear error rather than crashing or showing fake data.
* **Deployment Readiness**: The current branch features clear separation between the Next.js `app` directory and `backend` modules (`main.py` + `agents.py`).

## 4. Next Steps & Planning

To plan new features moving forward, consider the following vectors:
1. **Adding User Persistence**: Permitting users to save favorite locations, facts, or restaurants.
2. **Additional Specialized Agents**: E.g., a "Weather Oracle", a "Transport Guide" (Uber/Lyft pricing integrations), or an "Events Curator" (Ticketmaster API).
3. **Globe Interactivity Enhancements**: Making the Globe visualization react to real-time events, such as glowing hotspots for breaking news.
4. **Caching Layer**: Implementing a deeper cache (like Redis) on the FastAPI backend for previously queried cities to prevent duplicate API burning.
