# GeoLens — Claude Code Operating Guide

## Mission
Full-stack AI city intelligence app. Interactive 3D globe → user picks a city → two parallel product surfaces:
- **V1 /api/city-info** — 4 parallel agents return news/food/factoid/cost cards. **FROZEN. Do not modify.**
- **V2 /api/day-plan[/stream]** — sequential A2A pipeline (Scout→Curator→Navigator→Narrator) builds a streamed day plan from natural language + budget.

Target deployment: Google Cloud Run + Vercel. Currently local dev only.

---

## Repo Map

```
GeoLens/
├── CLAUDE.md                        ← this file
├── geolens_progress_report.md       ← V1 milestone history (read-only reference)
├── data/cost_of_living.csv          ← static cost data used by V1 Ledger agent
│
├── backend/
│   ├── main.py                      ← ALL FastAPI endpoints. Entry point.
│   ├── graph.py                     ← V1 parallel LangGraph (FROZEN)
│   ├── v1_agents.py                 ← V1 four agents (FROZEN)
│   ├── v2_graph.py                  ← V2 StateGraph + SSE streaming runner
│   ├── v2_state.py                  ← ⛔ DEAD FILE — never import. Use agents/state.py
│   ├── agents/
│   │   ├── state.py                 ← ✅ SCHEMA CONTRACT. Read before touching state keys.
│   │   ├── llm.py                   ← ✅ ONLY LLM import point. get_llm() + ainvoke_with_fallback()
│   │   ├── scout.py                 ← Agent 1: Ticketmaster+GNews+Foursquare+Gemini fallback
│   │   ├── curator.py               ← Agent 2: LLM itinerary builder
│   │   ├── navigator.py             ← Agent 3: ORS routing + haversine fallback
│   │   └── narrator.py              ← Agent 4: LLM synthesis → UI payload
│   ├── requirements.txt
│   ├── .env                         ← ⛔ SECRET — never read or modify
│   └── .venv/                       ← ⛔ never touch
│
└── geolens-app/                     ← Next.js 16.2.2 + React 19 + TailwindCSS v4
    ├── CLAUDE.md → AGENTS.md        ← ⚠️ Next.js 16 has breaking changes. Read node_modules/next/dist/docs/
    ├── app/                         ← App Router (page.tsx, layout.tsx, globals.css)
    ├── components/
    │   ├── GlobeCanvas.tsx          ← 3D globe, accepts mapPins prop
    │   ├── AgentSidebar.tsx         ← Tab switcher: Intelligence (V1) / Day Planner (V2)
    │   ├── ControlBar.tsx
    │   ├── AgentCards/              ← V1 cards (FROZEN): Factoid, Gastro, Ledger, News
    │   └── DayPlanner/              ← V2 UI: PlannerPanel, PlannerInput, TimelineCard, BudgetCard, NarratorCard
    └── lib/
        ├── api.ts                   ← fetchCityInfo(), streamDayPlan() SSE async iterator
        ├── types.ts                 ← ✅ FRONTEND CONTRACT. All V1+V2 TypeScript types.
        ├── mockData.ts              ← UI dev fallback mocks
        └── terminator.ts           ← AbortController helpers
```

---

## Backend/Frontend Contract

| Source of truth | File | What it governs |
|-----------------|------|-----------------|
| State schema | `backend/agents/state.py` | All PlannerState keys + TypedDicts |
| Frontend types | `geolens-app/lib/types.ts` | DayPlanResponse + all V2 response shapes |
| API shape | `backend/main.py` | Request/response models for both endpoints |

**Key state flow (V2):**
```
Scout   → writes: events: list[Event]
Curator → reads: events          writes: itinerary: list[ItineraryStop]
Navigator→reads: itinerary       writes: logistics: Logistics
Narrator → reads: everything     writes: plan: Plan
```
All agents append non-fatal errors to `state["errors"]` and return partial state — never raise.

---

## What Works

- `GET /health` → `{"version": "2.0.0"}`
- `POST /api/city-info` — V1 parallel pipeline, all 4 cards
- `POST /api/day-plan` — V2 blocking pipeline
- `POST /api/day-plan/stream` — V2 SSE (used by frontend)
- Gemini 2.5 Flash → Groq llama-3.3-70b-versatile auto-fallback on `429 RESOURCE_EXHAUSTED`
- Scout: if all 3 APIs return 0 events, Gemini generates plausible suggestions
- LangSmith tracing on all nodes and `@traceable` fetch functions
- Frontend: SSE progress streaming, Timeline, Budget donut, Narrator summary, map pins on globe

---

## Known Risks

| Risk | Severity | Detail |
|------|----------|--------|
| No caching | 🔴 | Every request hits all external APIs. Quota will be exhausted under real usage. |
| `v2_state.py` at repo root | 🟡 | Stale, superseded by `agents/state.py`. Do not import. |
| Navigator mode is hardcoded | 🟡 | ORS called once with `driving-car`. No multi-mode reasoning. Falls back to haversine if ORS fails. |
| No tests | 🟡 | State key mismatches and API schema changes fail silently until a full pipeline run. |
| Globe arc drawing | 🟢 | `mapPins` reach `GlobeCanvas.tsx` but sequential arcs between stops not yet drawn. |
| No MCP server | 🟢 | Planned but not started. |
| No production deployment | 🟢 | Local dev only. |

---

## Current Priorities (in order)

1. **`geolens-city-tools` MCP server** — `backend/mcp_server.py` using `fastmcp`. Expose Scout/Navigator fetch functions as `@mcp.tool()`. Do NOT wrap the whole pipeline.
2. **`run_stats` in state** — Add `run_stats: list[dict]` to `PlannerState` in `agents/state.py`. Each agent appends `{agent, tokens_used, latency_ms, model_used}`. Narrator passes through. Frontend shows collapsible "Debug Info" in `PlannerPanel.tsx`.
3. **Redis caching** — Wrap `run_v2_graph()` in `v2_graph.py`. Cache key: `sha256(city+date+sorted(preferences))`. TTL 6h. `REDIS_URL=redis://localhost:6379` in `.env`.
4. **Unit tests** — `backend/tests/` with `pytest` + `pytest-asyncio` + `respx`. 3 tests per agent: happy path, API failure, quota fallback.
5. **Cloud Run deployment** — Dockerfile for backend, Secret Manager for keys, Cloud Memorystore for Redis.

---

## Hard Constraints

- **V1 is frozen.** `v1_agents.py`, `graph.py`, `AgentCards/` — zero modifications. `/api/city-info` must keep working.
- **Never import from `backend/v2_state.py`.** Import from `backend/agents/state.py` only.
- **Never call `llm.ainvoke()` directly.** Always use `ainvoke_with_fallback()` from `agents/llm.py`.
- **Never instantiate `ChatGoogleGenerativeAI` or `ChatGroq` in agent files.** Always import `get_llm()` from `agents/llm.py`.
- **Agents never raise.** They catch, append to `state["errors"]`, return partial state.
- **`@traceable` on all new external API fetch functions.**
- **Schema first.** Any new `PlannerState` field: update `agents/state.py` → update `lib/types.ts` → then write agent code.
- **TailwindCSS v4 only.** No v3 `@apply`, no `tailwind.config.js`. Check v4 docs before writing CSS.
- **Next.js 16 APIs.** Check `node_modules/next/dist/docs/` before using any routing/data-fetching API.

---

## Commands

```bash
# Backend
cd backend && .venv\Scripts\activate
uvicorn main:app --reload --port 8000
python -c "from v2_graph import run_v2_graph; print('V2 graph OK')"
pytest tests/ -v                              # once test suite exists

# Frontend
cd geolens-app
npm run dev                                   # localhost:3000
npx tsc --noEmit                              # type check
npm run lint

# Smoke test SSE (verify all 4 agent_done events appear before complete)
curl -N -X POST http://localhost:8000/api/day-plan/stream \
  -H "Content-Type: application/json" \
  -d '{"city":"Tokyo","user_goal":"Museums and ramen","budget":80,"preferences":["museum","food"]}'

# Verify V1 still works after any backend change
curl -X POST http://localhost:8000/api/city-info \
  -H "Content-Type: application/json" \
  -d '{"city":"Tokyo","time_state":"day"}'
```

---

## Sensitive / Ignored Paths

Never read, write, or suggest edits to:
- `backend/.env` — all API keys
- `backend/.venv/` — Python venv
- `geolens-app/.env.local` — frontend env
- `geolens-app/node_modules/`
- `geolens-app/.next/`
- `backend/v2_state.py` — dead, stale schema file
- `backend/debug_dayplan*.json`, `backend/*_out.txt` — scratch debug artifacts

---

## Work Style

- Read the target file before editing it.
- After any `v2_graph.py` or agent change: run the SSE smoke test above.
- After any backend change: run the V1 curl check above.
- Prefer new functions/files over refactors of working code.
- When unsure about a Next.js 16 or TailwindCSS v4 API: ask, don't guess.
- Keep agent error handling non-fatal (append + return partial state).

---

## First-Task Checklist

Before writing any code in a new session:
- [ ] Read `backend/agents/state.py` — confirm state key names
- [ ] Read `geolens-app/lib/types.ts` — confirm frontend type names
- [ ] Confirm V1 endpoint is untouched (`graph.py` + `v1_agents.py` import paths unchanged)
- [ ] Identify which files the task touches and check for existing `@traceable` / `ainvoke_with_fallback` usage
