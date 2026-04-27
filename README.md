# 🌍 GeoLens

GeoLens is an interactive 3D globe web app with a multi-agent AI backend. Click any city to reveal two AI surfaces: a **City Intelligence** panel (news, food, facts, cost of living) powered by a parallel agent pipeline, and a **Day Planner** that builds a full personalized itinerary from natural language and a budget — streamed live as four AI agents work in sequence.

---

## 🏗 Architecture

GeoLens runs two independent AI pipelines behind a single FastAPI backend.

### V1 — City Intelligence (Parallel)

Four agents run in parallel via LangGraph and return instantly:

| Agent | Source | Output |
|---|---|---|
| 🗞️ Newshound | GNews API | Top local news stories |
| 🍱 Gastro-Guide | Foursquare API | Restaurant recommendations |
| 📚 Factoid | Wikipedia | Historical & cultural context |
| 💰 Local Ledger | CSV + open.er-api.com | Cost of living + currency |

### V2 — Day Planner (Sequential A2A Pipeline)

Four agents run in a strict sequential chain. Each agent reads only the state written by the previous one — removing any agent breaks all downstream outputs.

```
Scout → Curator → Navigator → Narrator
```

| Agent | Reads | Writes |
|---|---|---|
| 🔍 **Scout** | city, goal, budget | `events[]` from Ticketmaster + GNews + Foursquare; Gemini fallback if APIs return zero results |
| 🗂️ **Curator** | `events[]` | `itinerary[]` — conflict-free schedule within budget |
| 🗺️ **Navigator** | `itinerary[]` | `logistics` — ORS routing legs + transport cost; haversine fallback |
| 📖 **Narrator** | full state | `plan` — timeline, map pins, budget breakdown, citations |

**LLM stack**: Gemini 2.5 Flash (primary) with automatic failover to Groq Llama-3.3-70b on `429 RESOURCE_EXHAUSTED`. All agents are non-fatal — exceptions are caught, appended to a shared `errors[]` list, and partial state is returned.

**Streaming**: The Day Planner endpoint (`POST /api/day-plan/stream`) uses Server-Sent Events, emitting `agent_start` / `agent_done` / `complete` events so the UI can show live per-agent progress.

**Caching**: Pipeline results are cached in Redis (SHA-256 key on `city + date + preferences`, 6h TTL) to eliminate redundant LLM calls.

**Observability**: All agents and external API calls are instrumented with LangSmith `@traceable`. Per-agent `run_stats` (tokens, latency, model used) are tracked in shared state and surfaced in a collapsible debug panel.

---

### Frontend (Next.js 16 + React 19)

- **Globe**: Interactive 3D globe (`react-globe.gl` + Three.js) with day/night terminator overlay and animated city markers
- **Canvas Mode**: When a city is selected, the globe shrinks to a 168px mini-map (bottom-right); the Day Planner opens as a full-screen floating panel with real-time agent progress
- **Timeline Cards**: CSS 3D flip cards — front shows itinerary stop details, back shows insider tips + an OpenStreetMap embed; clicking "View Map" syncs the mini-map globe to that venue's coordinates
- **City Background**: Picsum-seeded city photo backdrop with a blurred light-leak overlay per selected city
- **Styling**: TailwindCSS v4 — no config file, arbitrary value syntax throughout

---

## ✨ Key Features

- **Retrieve-then-Generate**: Scout grounds Gemini's context with live API data before generation, minimizing hallucination
- **Resilient Pipeline**: Every agent handles failures gracefully — partial state is always returned, the pipeline never crashes entirely
- **Verifiable Citations**: Narrator outputs source URLs for every timeline item
- **Live Streaming UI**: Users see each agent complete in real time rather than waiting for the full pipeline
- **Dual-surface Design**: V1 instant intelligence cards coexist with V2 streamed day planning in the same UI

---

## 🚀 Getting Started

### Prerequisites

- Node.js v18+
- Python 3.10+
- API keys: `GEMINI_API_KEY`, `GROQ_API_KEY`, `GNEWS_API_KEY`, `TICKETMASTER_API_KEY`, `FOURSQUARE_API_KEY`, `ORS_API_KEY`
- Optional: `LANGSMITH_API_KEY` (tracing), `REDIS_URL` (caching, defaults to `redis://localhost:6379`)

### Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
# Add keys to .env
uvicorn main:app --reload --port 8000
```

Smoke test the V2 stream:
```bash
curl -N -X POST http://localhost:8000/api/day-plan/stream \
  -H "Content-Type: application/json" \
  -d '{"city":"Tokyo","user_goal":"Museums and ramen","budget":80,"preferences":["museum","food"]}'
```

### Frontend

```bash
cd geolens-app
npm install
npm run dev
# Open http://localhost:3000
```

---

## 🗂 Repo Structure

```
GeoLens/
├── backend/
│   ├── main.py              ← FastAPI endpoints (V1 + V2)
│   ├── graph.py             ← V1 parallel LangGraph
│   ├── v2_graph.py          ← V2 sequential pipeline + SSE streaming + Redis cache
│   └── agents/
│       ├── state.py         ← PlannerState TypedDict (schema contract)
│       ├── llm.py           ← LLM abstraction + failover logic
│       ├── scout.py
│       ├── curator.py
│       ├── navigator.py
│       └── narrator.py
└── geolens-app/
    ├── app/                 ← Next.js App Router
    ├── components/
    │   ├── GlobeCanvas.tsx
    │   ├── AgentSidebar.tsx
    │   ├── AgentCards/      ← V1 cards
    │   └── DayPlanner/      ← V2 UI (PlannerPanel, TimelineCard, BudgetCard, NarratorCard)
    └── lib/
        ├── api.ts           ← fetchCityInfo() + streamDayPlan() SSE iterator
        ├── types.ts         ← All TypeScript types
        └── map-context.tsx  ← Shared MapContext for mini-map fly-to sync
```

---

*Built to explore multi-agent orchestration with LangGraph, real-time SSE streaming, and full-stack AI deployment on GCP Cloud Run.*
