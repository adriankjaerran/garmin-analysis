// Activity list: dense sortable table + side panel with a mini map of the selected activity.
// ?place=<id> limits the list to activities with that main place (links from Places).
import { useMemo } from "preact/hooks";
import { qs, useApi, type Activity, type Meta } from "../lib/api";
import { setQuery, useLocation, navigate } from "../lib/router";
import { SPORT_LABEL, sportColor } from "../lib/format";
import { Table } from "../components/Table";
import { TracksMap } from "../components/TracksMap";
import { activityCols, ActivityKV } from "../components/ActivityBits";

function MiniPanel({ a }: { a: Activity }) {
  const tr = useApi<{ lat: number[]; lon: number[] }>(a.track ? `/api/activities/${a.id}/track?format=columns&max_points=1500` : null);
  const tracks = useMemo(() => tr.data ? [{ id: a.id, sport: a.sport, coords: tr.data.lon.map((x, i) => [x, tr.data!.lat[i]] as [number, number]) }] : [], [tr.data, a.id]);
  return (
    <>
      <TracksMap tracks={tracks} style={{ height: "320px", flex: "none" }} compact empty={tr.loading ? "Loading…" : a.track ? "No GPS track" : "No GPS for this activity"} />
      <div class="pane">
        <div class="head"><h2 style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name ?? a.type}</h2></div>
        <ActivityKV a={a} />
        <div class="head" style={{ marginTop: "12px" }}>
          <a class="btn" href={`/activities/${a.id}`}>Open activity</a>
          <a class="btn" href={`/day/${a.date}`}>Day view</a>
          {a.fit && <a class="btn" href={`/compare?suggest=${a.id}`} title="Compare with similar sessions (same workout / route)">Compare</a>}
          <a class="btn" href={`/activities/${a.id}?go=closest`} title="Closest activities (same workout, route, place)">Closest</a>
        </div>
      </div>
    </>
  );
}

export function ActivitiesPage() {
  const { query } = useLocation();
  const sport = query.get("sport") || "", year = query.get("year") || "", q = query.get("q") || "", sel = query.get("sel");
  const place = query.get("place") || "";
  const meta = useApi<Meta>("/api/meta").data;
  const { data, error } = useApi<{ n: number; activities: Activity[] }>(
    "/api/activities" + qs({ sport, q, place, from: year && `${year}-01-01`, to: year && `${year}-12-31` }));
  const rows = data?.activities ?? [];
  const selected = rows.find((a) => a.id === sel) ?? null;
  const cols = useMemo(() => activityCols(), []);
  const totalKm = rows.reduce((s, a) => s + (a.dist_km || 0), 0);

  return (
    <main class="full">
      <div class="toolbar">
        <h1>Activities</h1>
        <input type="search" placeholder="Search name, type, place, 6×1000…" value={q} style={{ width: "260px" }}
          onInput={(e) => setQuery({ q: (e.target as HTMLInputElement).value })} />
        <div class="chips">
          <button class={"chip" + (!sport ? " on" : "")} onClick={() => setQuery({ sport: null })}>All</button>
          {meta?.sports.map((s) => (
            <button key={s.id} class={"chip" + (sport === s.id ? " on" : "")} onClick={() => setQuery({ sport: sport === s.id ? null : s.id })}>
              <span class="dot" style={{ background: sportColor(s.id) }} />{SPORT_LABEL[s.id]}
            </button>
          ))}
        </div>
        {place && <button class="chip on" onClick={() => setQuery({ place: null })} title="Remove the place filter">
          Place: {rows[0]?.place === place ? rows[0].place_name ?? place : place} ×</button>}
        <select value={year} onChange={(e) => setQuery({ year: (e.target as HTMLSelectElement).value })}>
          <option value="">All years</option>
          {meta?.years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <span class="count">{error ?? `${rows.length} activities, ${Math.round(totalKm).toLocaleString("en-GB")} km`}</span>
      </div>
      <div class="split">
        <div class="left">
          <Table cols={cols} rows={rows} rowKey={(a) => a.id} selected={sel} follow maxHeight="100%"
            onRow={(a) => (a.id === sel ? navigate(`/activities/${a.id}`) : setQuery({ sel: a.id }))}
            empty={data ? "No activities match." : "Loading…"} />
        </div>
        <div class="right">
          {selected ? <MiniPanel key={selected.id} a={selected} /> :
            <div class="pane empty">Select a row to see its map. Click it again (or Open) for the full activity.</div>}
        </div>
      </div>
    </main>
  );
}
