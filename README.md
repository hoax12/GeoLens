# 🌍 GeoLens

GeoLens is an interactive globe web application powered by a sophisticated multi-agent AI backend. It dynamically gathers, synthesizes, and displays real-time localized insights—including news, culinary guides, historical facts, and cost of living data—for any city on Earth.

## 🏗 Architecture

The project is strategically structured into a sleek frontend and an agentic backend working in tandem to fetch and format intelligence:

### Frontend (Next.js & React)
- **Framework**: Built on Next.js 15+ (App Router).
- **Styling**: TailwindCSS combined with the Vercel Geist Font for a premium feel.
- **Visualization**: Immersive interactive 3D globe visualization (`GlobeCanvas.tsx`).
- **Dashboard UI**: A custom multi-card widget layout that utilizes progressive disclosure. It gracefully handles agent errors with "Honest Service Fallbacks," displaying clean "Service Offline" states instead of failing out.

### Backend (Python & FastAPI)
- **API Framework**: High-performance routing with FastAPI.
- **Agent Orchestration**: Powered by LangGraph to manage an asynchronous 4-agent parallel intelligence pipeline.
- **LLM Engine**: Driven by `ChatGroq` (`llama-3.1-8b-instant`) to rapidly structure and parse external data into JSON.
- **Sub-Agents**:
  - 🗞️ **Newshound**: Queries the GNews API for localized recent stories.
  - 🍱 **Gastro-Guide**: Interfaces with the Foursquare API to suggest top restaurants and cafes.
  - 📚 **Factoid**: Interrogates Wikipedia to surface distinct historical and cultural context.
  - 💰 **Local Ledger**: Custom cost-of-living CSV parser combined with live currency exchange rates (`open.er-api.com`).

## ✨ Key Features
- **Fetch-and-Inject AI Pattern**: Guarantees high intelligence accuracy and minimizes hallucination by grounding the LLM's context window tightly with real live API data before generation.
- **Verifiable Citations**: Every generated insight maintains a source URL linking out to Google Maps, Wiki, or News sources, enhancing data transparency.
- **Resilient UI States**: Built with strict error boundaries to detect API rate limits or failures dynamically. 

## 🚀 Getting Started

### Prerequisites
- Node.js (v18+)
- Python (3.9+)
- Valid API Keys for Groq, GNews, and Foursquare

### Backend Setup
1. Navigate to the `backend/` directory.
2. Create your virtual environment and install dependencies.
3. Configure your `.env` file with the required API keys (reference standard variables like `GROQ_API_KEY`, `GNEWS_API_KEY`, etc.).
4. Run the development server:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

### Frontend Setup
1. Navigate to the `geolens-app/` directory.
2. Install Node dependencies:
   ```bash
   npm install
   ```
3. Run the development server:
   ```bash
   npm run dev
   ```
4. Access the application at `http://localhost:3000`.

## 🔮 Future Roadmap
- **User Persistence**: Allow users to save favorite cities, facts, or restaurants.
- **Additional Specialized Agents**: Exploring a "Weather Oracle", a "Transport Guide" for rideshare pricing, and an "Events Curator".
- **Dynamic Globe Iteration**: Making the 3D globe react to real-time events, such as glowing hotspots for breaking local news.
- **Caching Layer**: Integrating Redis on the backend to reduce redundant API queries.

---
*Built as a prototype exploring multi-agent orchestration via LangGraph attached to an interactive web UI.*
