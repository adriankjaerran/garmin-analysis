import { render } from "preact";
import { useEffect } from "preact/hooks";
import { useApi, type Meta } from "./lib/api";
import { interceptLinks, match, navigate, useLocation } from "./lib/router";
import { MapPage } from "./pages/MapPage";
import { ActivitiesPage } from "./pages/ActivitiesPage";
import { ActivityPage } from "./pages/ActivityPage";
import { DayPage } from "./pages/DayPage";
import { PlacesPage } from "./pages/PlacesPage";
import { HomePage } from "./pages/HomePage";
import { FitnessPage } from "./pages/FitnessPage";
import { ComparePage } from "./pages/ComparePage";
import "./styles.css";

// Route table. `redirect` entries keep old URLs working (same query).
const ROUTES: { path: string; nav?: string; render: (p: Record<string, string>) => preact.JSX.Element | null }[] = [
  { path: "/", nav: "home", render: () => <HomePage /> },
  { path: "/map", nav: "map", render: () => <MapPage /> },
  { path: "/day", nav: "day", render: () => <DayPage date={null} /> },
  { path: "/day/:date", nav: "day", render: (p) => <DayPage date={p.date} /> },
  { path: "/activities", nav: "activities", render: () => <ActivitiesPage /> },
  { path: "/activities/:id", nav: "activity", render: (p) => <ActivityPage id={p.id} /> },
  { path: "/places", nav: "places", render: () => <PlacesPage /> },
  // Find & group was merged into Activities: same filters, view=groups, gk, g, pick
  { path: "/search", nav: "activities", render: () => redirect("/activities") },
  { path: "/compare", nav: "compare", render: () => <ComparePage /> },
  { path: "/fitness", nav: "fitness", render: () => <FitnessPage /> },
];

/** replace the URL with `path` + the current query (after render, so the router picks it up) */
function Redirect({ to }: { to: string }) {
  useEffect(() => { navigate(to + location.search, true); }, [to]);
  return null;
}
const redirect = (to: string) => <Redirect to={to} />;

function Nav({ current, meta, activityId }: { current: string; meta: Meta | null; activityId: string | null }) {
  const tab = (id: string, href: string, label: string, soon = false) => (
    <a class={"tab" + (current === id ? " current" : "")} href={href}>
      {label}{soon && <span class="soon">soon</span>}
    </a>
  );
  return (
    <nav class="side">
      <div class="who">{meta ? meta.person[0].toUpperCase() + meta.person.slice(1) : "…"}</div>
      <a class={"step home" + (current === "home" ? " current" : "")} href="/">Home</a>
      <span class="step">Explore</span>
      {tab("activities", "/activities", "Activities")}
      {current === "activity" && activityId && <a class="tab sub current" href={`/activities/${activityId}`}>Activity</a>}
      {tab("day", "/day", "Calendar & day")}
      {tab("map", "/map", "Map")}
      {tab("places", "/places", "Places & routes")}
      <span class="step">Analysis</span>
      {tab("compare", "/compare", "Compare")}
      {tab("fitness", "/fitness", "Fitness")}
      {meta && (
        <div class="built">
          {meta.n_activities} activities, {meta.n_tracks} with GPS<br />
          {meta.first} to {meta.last}
        </div>
      )}
    </nav>
  );
}

let lastActivity: string | null = null;

function App() {
  const loc = useLocation();
  const meta = useApi<Meta>("/api/meta").data;
  let hit: { r: (typeof ROUTES)[number]; p: Record<string, string> } | null = null;
  for (const r of ROUTES) {
    const p = match(r.path, loc.path);
    if (p) { hit = { r, p }; break; }
  }
  if (hit?.r.nav === "activity") lastActivity = hit.p.id;
  return (
    <div class="app">
      <Nav current={hit?.r.nav ?? ""} meta={meta} activityId={lastActivity} />
      {hit ? hit.r.render(hit.p) : <main><h1>Not found</h1><p class="note">No page at {loc.path}. <a href="/">Go home</a>.</p></main>}
    </div>
  );
}

interceptLinks();
render(<App />, document.getElementById("app")!);
