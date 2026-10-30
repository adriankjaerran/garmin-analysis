// Placeholder for pages that later workers implement. Each lists its planned API so the page and the
// backend router (app/server/routers/<kind>.py) can be filled in independently.
import { useLocation } from "../lib/router";

const INFO = {
  search: {
    title: "Search",
    text: "Find activities by place, route, workout category, interval signature, rep count and rep length; and the nearest activities to any session.",
    api: ["GET /api/search?place=&route=&family=&sig=&rep_count=&rep_dist=&from=&to=", "GET /api/nearest/{id}?k=10&preset=workout|route"],
    source: "scripts/similarity.py → data/<person>/similarity_index.json",
  },
  compare: {
    title: "Compare",
    text: "Side by side: metrics, aligned pace / speed and HR, and leg-by-leg comparison when the sessions share a route.",
    api: ["GET /api/compare?ids=a,b,c"],
    source: "groups.json routes + the series cache",
  },
  fitness: {
    title: "Fitness",
    text: "Shape per discipline over time (running, skiing, …).",
    api: ["GET /api/fitness"],
    source: "scripts/fitness_model.py → data/<person>/fitness.json",
  },
} as const;

export function StubPage({ kind }: { kind: keyof typeof INFO }) {
  const { query } = useLocation();
  const i = INFO[kind];
  return (
    <main>
      <section class="first">
        <div class="head"><h1>{i.title}</h1><span class="sub">Not built yet</span></div>
        <p>{i.text}</p>
        <dl class="kv two">
          <dt>Planned API</dt><dd class="mono">{i.api.map((a) => <div key={a}>{a}</div>)}</dd>
          <dt>Data</dt><dd class="mono">{i.source}</dd>
          {query.toString() && <><dt>This URL</dt><dd class="mono">?{query.toString()}</dd></>}
        </dl>
      </section>
    </main>
  );
}
