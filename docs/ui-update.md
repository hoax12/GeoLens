Mission: Immersive City Canvas & Dual-Map Orchestration
Goal
Transition GeoLens from a sidebar-centric dashboard to an immersive, "Forward-Deployed" agentic experience. When a city is selected, the UI should pivot to a centered "Canvas" view with a dynamic background, a persistent mini-map, and interactive flip-cards.

1. Global Layout & State Transitions
Context Layer: Implement/Update MapContext to manage activeCity, isPlanningMode, and focusedCoordinates.

Background Engine: Create a CityBackground component.

Fetch city-specific landscapes from Unsplash API.

Overlay a "Light Leak" div: Fixed position, linear-gradient(from-orange-500/20 via-transparent to-purple-500/20), with blur-3xl.

Mini-Map Transition: - When isPlanningMode is true, the Globe component must shrink and translate to the bottom-right corner (approx 200px glassmorphic circle).

Use framer-motion for a coordinated scale/position shift.

2. Centered "Canvas" Day Planner
Positioning: Move DayPlanner.tsx from the sidebar to a central floating panel (w-[80%] max-w-5xl).

Horizontal Timeline: Refactor the vertical list into a horizontal, scrollable row of Flip Cards.

The Flip Card Component:

Front: Time, Category Icon (Lucide), and Stop Name.

Back: - Detail Map: A small, high-contrast static map preview (Mapbox/Google) showing the stop's location.

Citations: "Source" link and "Insider Tip" toggle.

Interaction: Use CSS perspective and rotateY for 3D flip.

3. "Context + Detail" Sync Logic
The "View Map" Event: Clicking "View Map" on a card back must trigger a dual-sync:

Detail: Reveal the static map on the card back.

Context: Update MapContext to trigger a flyTo animation on the 3D Mini-map, centering it on the {lat, lng} of the selected stop.

Observability: Ensure the Mini-map includes a "Reset City View" button to zoom back out to the full city boundaries.

4. UI/UX Refinement (Genz-Swag Aesthetic)
Glassmorphism: All panels must use backdrop-blur-xl, bg-white/10, and border-white/20.

Sidebar Minimization: When isPlanningMode is active, collapse the secondary agents (News, Gastro, Factoid) into small, vertical glassmorphic tabs on the far left edge.

Coordination: Coordinate the timing of the card flip with the globe's rotation so they feel like a single, responsive system.

DOne - >
  New files:                                                                                                                                                                                                          
  - lib/map-context.tsx — MapProvider + useMapContext() hook; shares focusCoords between flip cards and the mini-map globe                                                                                            
  - components/CityBackground.tsx — Picsum photo backdrop (fades in on load) + fixed orange→purple light-leak gradient blob                                                                                           
                                                                                                                                                                                                                      
  Modified files:                                                                                                                                                                                                     
  - app/globals.css — Added .flip-card-inner/.front/.back 3D CSS classes + canvas-slide-up, mini-map-appear, vertical-tabs-in keyframes
  - components/GlobeCanvas.tsx — New miniMap prop (220×220, no loading overlay) + focusCoords prop triggers pointOfView() fly-to animation
  - components/DayPlanner/TimelineCard.tsx — Full flip-card rewrite: front shows time/icon/name, back shows insider tip + source link + "View Map" button (calls setFocusCoords + toggles OSM iframe)
  - components/DayPlanner/PlannerPanel.tsx — canvasMode prop removes flex-1 overflow-y-auto so parent handles scrolling
  - components/AgentSidebar.tsx — "Day Planner" tab now calls onOpenCanvas() instead of rendering inline; when canvasActive=true, renders collapsed vertical glassmorphic icon-tabs on the left edge
  - app/page.tsx — Wrapped in MapProvider; full-screen globe when no city; 200px circular mini-map bottom-right + CityBackground + centered canvas panel (w-full max-w-5xl) when city is active
