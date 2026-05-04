# 🌍 GeoLens

GeoLens is an interactive 3D globe web app with a multi-agent AI backend. Click any city to reveal two AI surfaces: a **City Intelligence** panel (news, food, facts, cost of living, safety briefing) powered by a parallel agent pipeline, and a **Day Planner** that builds a full personalized itinerary from natural language and a budget — streamed live as four AI agents work in sequence.

---

## 🏗 Architecture

GeoLens runs two independent AI pipelines behind a single FastAPI backend.

### V1 — City Intelligence (Parallel)

Five agents run in parallel via LangGraph and return instantly:

| Agent | Source | Output |
|---|---|---|
| 🗞️ Newshound | GNews API | Top local news stories |
| 🍱 Gastro-Guide | Foursquare API | Restaurant recommendations |
| 📚 Factoid | Wikipedia | Historical & cultural context |
| 💰 Local Ledger | CSV + open.er-api.com | Cost of living + currency |
| 🛡️ Safety Briefing | GNews + LLM | Actionable tourist safety tips |

> V1 agents are frozen. The Safety Briefing is served from a separate `/api/city-safety` endpoint and rendered alongside V1 cards in the same Intelligence tab.

### V2 — Day Planner (Sequential A2A Pipeline)

Four agents run in a strict sequential chain. Each agent reads only the state written by the previous one — removing any agent breaks all downstream outputs.

```
Scout → Curator → Navigator → Narrator
```

| Agent | Reads | Writes |
|---|---|---|
| 🔍 **Scout** | city, goal, budget, preferences | `events[]` — Ticketmaster + GNews + Foursquare; Gemini fallback if all APIs return zero results |
| 🗂️ **Curator** | `events[]`, preferences | `itinerary[]` — conflict-free schedule within budget, with `notes` (reasoning) and `peak_warning` (crowd timing) per stop; respects Local Mode |
| 🗺️ **Navigator** | `itinerary[]`, budget | `itinerary[]` (geographically reordered) + `logistics` — ORS routing legs + transport cost; haversine fallback |
| 📖 **Narrator** | full state | `plan` — timeline, map pins, budget breakdown, citations |

**LLM stack**: Groq `llama-3.3-70b-versatile` (primary, ~1–3 s/call) with automatic failover to Gemini `2.0-flash` on `429 rate_limit`. All agents are non-fatal — exceptions are caught, appended to a shared `errors[]` list, and partial state is always returned.

**Streaming**: The Day Planner endpoint (`POST /api/day-plan/stream`) uses Server-Sent Events, emitting `agent_start` / `agent_done` / `complete` events so the UI shows live per-agent progress.

**Caching**: Pipeline results are cached in Redis (SHA-256 key on `city + date + preferences`, 6 h TTL) to eliminate redundant LLM calls.

**Observability**: All agents and external API calls are instrumented with LangSmith `@traceable`. Per-agent `run_stats` (tokens, latency, model) are tracked in shared state and surfaced in a collapsible Debug Info panel.

---

### Frontend (Next.js 16 + React 19)

- **Globe**: Interactive 3D globe (`react-globe.gl` + Three.js) with day/night terminator overlay, animated city markers, and animated route arcs between itinerary stops
- **Intelligence view**: When a city is selected the globe fills the right panel (`left: 380px`). The Intelligence tab shows V1 agent cards + Safety Briefing; the Day Planner tab opens the V2 canvas
- **Canvas Mode**: Day Planner opens as a full-screen floating panel with real-time agent progress; a 192 px circular mini-map globe appears bottom-right, syncing to the active venue when "View Map" is clicked
- **Local Mode toggle**: Vibe selector in the Day Planner input — "Tourist" (default, blue) vs "🏡 Local Mode" (emerald). Local Mode injects bias instructions into the Curator prompt to prefer independent, neighbourhood venues
- **Timeline Cards**: CSS 3D flip cards — front shows stop details + an amber `⏰` peak-hour warning when the scheduled slot is historically busy; back shows insider tip + Source link + Google Maps button that syncs the globe
- **Budget Meter**: Two-segment progress bar above the timeline (blue = events, amber = transport) with a running "$X left / Over by $X" counter
- **Safety Briefing**: Accordion card in the Intelligence tab — each tip shows severity (HIGH / MED / LOW) in rose / amber / emerald; loaded independently so V1 cards never block on it
- **City Backgrounds**: City-specific dark radial-gradient auras tuned to each city's identity (Tokyo: violet/magenta; New York: amber/burnt; London: steel blue/teal; Paris: gold/rose; Sydney: ocean blue/teal)
- **Styling**: TailwindCSS v4 — no config file, arbitrary value syntax throughout

---

## ✨ Key Features

| Feature | Detail |
|---|---|
| Retrieve-then-Generate | Scout grounds the LLM context with live API data before generation, minimising hallucination |
| Resilient pipeline | Every agent catches exceptions, appends to `errors[]`, returns partial state — the pipeline never crashes entirely |
| Neighbourhood clustering | Navigator applies greedy nearest-neighbour reordering before routing, minimising unnecessary cross-city travel |
| Peak-hour warnings | Curator flags each stop with a crowd-timing warning when the scheduled slot overlaps a known busy period |
| Local Mode | Vibe toggle biases the Curator toward independent, off-the-beaten-path venues and away from tourist traps |
| Budget transparency | Per-stop cost badges + running budget meter show exactly how the budget is consumed |
| Safety Briefing | GNews-grounded LLM generates 4–5 actionable safety tips per city; static fallback ensures it always renders |
| Verifiable citations | Narrator outputs source URLs; invalid/hallucinated URLs fall back to a Google Search for the venue |
| Live streaming UI | Users see each agent complete in real time via SSE rather than waiting for the full pipeline |
| Dual-surface design | V1 instant intelligence cards coexist with V2 streamed day planning in the same sidebar |

---

## 🚀 Getting Started

### Prerequisites

- Node.js v18+
- Python 3.10+
- API keys in `backend/.env`:

```
GROQ_API_KEY=...            # Primary LLM (~1-3s/call)
GOOGLE_API_KEY=...          # Gemini fallback
GNEWS_API_KEY=...
TICKETMASTER_API_KEY=...
FOURSQUARE_API_KEY=...
OPENROUTESERVICE_API_KEY=...
LANGSMITH_API_KEY=...       # Optional — tracing
REDIS_URL=redis://localhost:6379  # Optional — caching
```

### Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Smoke test V2 stream (expect four `agent_done` events then `complete`):
```bash
curl -N -X POST http://localhost:8000/api/day-plan/stream \
  -H "Content-Type: application/json" \
  -d '{"city":"Tokyo","user_goal":"Museums and ramen","budget":80,"preferences":["museum","food"]}'
```

Verify V1 still works:
```bash
curl -X POST http://localhost:8000/api/city-info \
  -H "Content-Type: application/json" \
  -d '{"city":"Tokyo","time_state":"day"}'
```

Smoke test Safety Briefing:
```bash
curl -X POST http://localhost:8000/api/city-safety \
  -H "Content-Type: application/json" \
  -d '{"city":"London"}'
```

### Frontend

```bash
cd geolens-app
npm install
npm run dev        # http://localhost:3000
npx tsc --noEmit   # type check
```

---

## 🗂 Repo Structure

```
GeoLens/
├── backend/
│   ├── main.py              ← FastAPI endpoints (V1 + V2 + Safety)
│   ├── graph.py             ← V1 parallel LangGraph (frozen)
│   ├── v2_graph.py          ← V2 sequential pipeline + SSE streaming + Redis cache
│   └── agents/
│       ├── state.py         ← PlannerState TypedDict (schema contract)
│       ├── llm.py           ← Groq primary + Gemini fallback; ainvoke_with_fallback()
│       ├── scout.py         ← Ticketmaster + GNews + Foursquare; Gemini fallback
│       ├── curator.py       ← Itinerary builder; peak_warning + Local Mode support
│       ├── navigator.py     ← Nearest-neighbour clustering + ORS routing
│       ├── narrator.py      ← Plan synthesis + citations
│       └── safety.py        ← Safety briefing; GNews + LLM synthesis
└── geolens-app/
    ├── app/                 ← Next.js App Router (page.tsx, globals.css)
    ├── components/
    │   ├── GlobeCanvas.tsx  ← 3D globe, mini-map mode, route arcs
    │   ├── AgentSidebar.tsx ← Intelligence + Day Planner tab switcher
    │   ├── CityBackground.tsx ← City-specific gradient auras
    │   ├── SafetyCard.tsx   ← Accordion safety tips with severity badges
    │   ├── AgentCards/      ← V1 cards (frozen): News, Gastro, Factoid, Ledger
    │   └── DayPlanner/      ← V2 UI:
    │       ├── PlannerPanel.tsx   ← SSE orchestration + debug info
    │       ├── PlannerInput.tsx   ← Goal / budget / vibe toggle / interests
    │       ├── TimelineCard.tsx   ← Flip cards + budget meter + peak warnings
    │       ├── BudgetCard.tsx     ← Donut chart breakdown
    │       └── NarratorCard.tsx   ← Day summary + citations
    └── lib/
        ├── api.ts           ← fetchCityInfo() + fetchSafetyBriefing() + streamDayPlan()
        ├── types.ts         ← All TypeScript types (V1 + V2 + Safety)
        ├── map-context.tsx  ← Shared MapContext for mini-map fly-to sync
        └── mockData.ts      ← UI dev fallback city data
```

---

*Built to explore multi-agent orchestration with LangGraph, real-time SSE streaming, and full-stack AI deployment on GCP Cloud Run.*
