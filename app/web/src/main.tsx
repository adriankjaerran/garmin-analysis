import { render } from "preact";
import { useApi, type Meta } from "./lib/api";
import { interceptLinks, match, navigate, useLocation } from "./lib/router";
import { MapPage } from "./pages/MapPage";
import { ActivitiesPage } from "./pages/ActivitiesPage";
import { ActivityPage } from "./pages/ActivityPage";
import { DayPage } from "./pages/DayPage";
import { PlacesPage } from "./pages/PlacesPage";
import { StubPage } from "./pages/StubPage";
import "./styles.css";

// Route table. Extension points (search / compare / fitness) render StubPage until implemented:
// replace the entry with the real page component.
const ROUTES: { path: string; nav?: string; render: (p: Record<string, string>) => preact.JSX.Element }[] = [
  { path: "/map", nav: "map", render: () => <MapPage /> },
  { path: "/day", nav: "day", render: () => <DayPage date={null} /> },
  { path: "/day/:date", nav: "day", render: (p) => <DayPage date={p.date} /> },
  { path: "/activities", nav: "activities", render: () => <ActivitiesPage /> },
  { path: "/activities/:id", nav: "activity", render: (p) => <ActivityPage id={p.id} /> },
  { path: "/places", nav: "places", render: () => <PlacesPage /> },
  { path: "/search", nav: "search", render: () => <StubPage kind="search" /> },
  { path: "/compare", nav: "compare", render: () => <StubPage kind="compare" /> },
  { path: "/fitness", nav: "fitness", render: () => <StubPage kind="fitness" /> },
];

function Nav({ current, meta, activityId }: { current: string; meta: Meta | null; activityId: string | null }) {
  const tab = (id: string, href: string, label: string, soon = false) => (
    <a class={"tab" + (current === id ? " current" : "")} href={href}>
      {label}{soon && <span class="soon">soon</span>}
    </a>
  );
  return (
    <nav class="side">
      <div class="who">{meta ? meta.person[0].toUpperCase() + meta.person.slice(1) : "…"}</div>
      <a class="step" href="/map">Explore</a>
      {tab("map", "/map", "Map")}
      {tab("day", "/day", "Calendar & day")}
      <a class="step" href="/activities">Activities</a>
      {tab("activities", "/activities", "List")}
      {activityId && tab("activity", `/activities/${activityId}`, "Activity")}
      <a class="step" href="/places">Places</a>
      {tab("places", "/places", "Places & routes")}
      <a class="step" href="/search">Analysis</a>
      {tab("search", "/search", "Search", true)}
      {tab("compare", "/compare", "Compare", true)}
      {tab("fitness", "/fitness", "Fitness", true)}
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
  if (loc.path === "/") { navigate("/map", true); return null; }
  let hit: { r: (typeof ROUTES)[number]; p: Record<string, string> } | null = null;
  for (const r of ROUTES) {
    const p = match(r.path, loc.path);
    if (p) { hit = { r, p }; break; }
  }
  if (hit?.r.nav === "activity") lastActivity = hit.p.id;
  return (
    <div class="app">
      <Nav current={hit?.r.nav ?? ""} meta={meta} activityId={lastActivity} />
      {hit ? hit.r.render(hit.p) : <main><h1>Not found</h1><p class="note">No page at {loc.path}. <a href="/map">Go to the map</a>.</p></main>}
    </div>
  );
}

interceptLinks();
render(<App />, document.getElementById("app")!);
