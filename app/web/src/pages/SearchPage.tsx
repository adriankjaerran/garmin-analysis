// Find & group: filter activities by sport, place / region, route, workout hierarchy, reps (count, length,
// rest), distance and dates; results as a sortable table + map of all result tracks; or browse the groups
// (places, regions, routes, workout tree) that the current filters leave. Every control lives in the URL.
import { useEffect, useMemo, useState } from "preact/hooks";
import { qs, useApi } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { SPORT_LABEL, dur, km, num, pace, kmh, sportColor } from "../lib/format";
import { FILTER_KEYS, compareUrl, filterQuery, repLen, mmss, type Group, type SearchOptions, type SearchResult, type SearchRow, type TreeNode } from "../lib/search";
import { Table, type Col } from "../components/Table";
import { TracksMap } from "../components/TracksMap";
import { GroupsMap, type GLine } from "../components/GroupsMap";
import { SportDot } from "../components/ActivityBits";

// ------------------------------------------------------------------ small controls
/** text input that commits to the URL after a pause (or Enter / blur) */
function QInput({ k, value, placeholder, width, title, list }: { k: string; value: string; placeholder?: string; width: number; title?: string; list?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const t = setTimeout(() => setQuery({ [k]: v.trim() }), 450);
    return () => clearTimeout(t);
  }, [v]);
  const commit = () => v !== value && setQuery({ [k]: v.trim() });
  return <input type={k === "q" ? "search" : "text"} value={v} placeholder={placeholder} title={title} list={list}
    style={{ width: width + "px" }} onInput={(e) => setV((e.target as HTMLInputElement).value)}
    onKeyDown={(e) => e.key === "Enter" && commit()} onBlur={commit} />;
}

function Sel({ k, value, opts, all, width }: { k: string; value: string; opts: { id: string; label: string }[]; all: string; width?: number }) {
  const known = !value || opts.some((o) => o.id === value);
  return (
    <select value={value} style={width ? { width: width + "px" } : undefined} onChange={(e) => setQuery({ [k]: (e.target as HTMLSelectElement).value })}>
      <option value="">{all}</option>
      {!known && <option value={value}>{value}</option>}
      {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  );
}

function Seg2({ k, value, a, b, title }: { k: string; value: string; a: [string, string]; b: [string, string]; title?: string }) {
  return (
    <div class="seg" title={title}>
      <button class={value !== b[0] ? "on" : ""} onClick={() => setQuery({ [k]: null })}>{a[1]}</button>
      <button class={value === b[0] ? "on" : ""} onClick={() => setQuery({ [k]: b[0] })}>{b[1]}</button>
    </div>
  );
}

const Fld = ({ label, children, title }: { label: string; children: any; title?: string }) => (
  <label class="fld" title={title}><span>{label}</span>{children}</label>
);

// ------------------------------------------------------------------ filter bar
function FilterBar({ query, opts }: { query: URLSearchParams; opts: SearchOptions | null }) {
  const g = (k: string) => query.get(k) || "";
  const sig = g("signature");
  const exact = (opts?.exact ?? []).filter((e) => !sig || sig.startsWith("~") ? e.id.includes(sig.replace(/^~/, "")) : e.id.startsWith(sig + " ·"));
  const active = FILTER_KEYS.filter((k) => k !== "q" && k !== "sport" && query.get(k)).length;
  return (
    <div class="fbar">
      <Fld label="Place" title="Main place, start, end or any touched place (≥ 5 % of the time or 3 min); 'main' = ≥ 40 % of the time">
        <Sel k="place" value={g("place")} all="Any" width={140}
          opts={(opts?.places ?? []).map((p) => ({ id: p.id, label: `${p.id} (${p.n})` }))} />
        <Seg2 k="place_mode" value={g("place_mode")} a={["", "touched"]} b={["main", "main"]} />
      </Fld>
      <Fld label="Region" title="Marka / Østmarka / Bymarka polygons, else the Garmin municipality">
        <Sel k="region" value={g("region")} all="Any" width={130}
          opts={(opts?.regions ?? []).map((p) => ({ id: p.id, label: `${p.id} (${p.n})` }))} />
        <Seg2 k="region_mode" value={g("region_mode")} a={["", "touched"]} b={["main", "main"]} />
      </Fld>
      <Fld label="Route">
        <Sel k="route" value={g("route")} all="Any" width={150}
          opts={(opts?.routes ?? []).map((r) => ({ id: r.id, label: `${r.id} ${r.name} (${r.n})` }))} />
      </Fld>
      <Fld label="Workout">
        <Sel k="family" value={g("family")} all="Any family" width={140}
          opts={(opts?.families ?? []).map((f) => ({ id: f.id, label: `${f.label ?? f.id} (${f.n})` }))} />
        <Sel k="category" value={g("category")} all="Any category" width={130}
          opts={(opts?.categories ?? []).map((f) => ({ id: f.id, label: `${f.id.replace("_", " ").replace("?", "")} (${f.n})` }))} />
      </Fld>
      <Fld label="Signature" title="Exact signature (6×1000) or ~substring (~×1000, ~4:00)">
        <QInput k="signature" value={sig} placeholder="6×1000 / ~×1000" width={120} list="sigs" />
        <datalist id="sigs">{(opts?.signatures ?? []).map((s) => <option key={s.id} value={s.id}>{s.n}</option>)}</datalist>
        <Sel k="exact_key" value={g("exact_key")} all="Any rest" width={170} opts={exact.map((e) => ({ id: e.id, label: `${e.id} (${e.n})` }))} />
      </Fld>
      <Fld label="Reps" title="Rep count of one part: 6 or 5-7">
        <QInput k="rep_count" value={g("rep_count")} placeholder="5-7" width={56} />
      </Fld>
      <Fld label="Rep length" title="Distance (1000m, 900-1100) or time (4:00, 3:30-4:30, 90s); single values ± tolerance">
        <QInput k="rep_len" value={g("rep_len")} placeholder="1000m / 4:00" width={96} />
        <select value={g("tol") || "0.1"} onChange={(e) => setQuery({ tol: (e.target as HTMLSelectElement).value === "0.1" ? null : (e.target as HTMLSelectElement).value })} title="Tolerance for single values">
          {["0.05", "0.1", "0.2"].map((t) => <option key={t} value={t}>± {Math.round(+t * 100)} %</option>)}
        </select>
      </Fld>
      <Fld label="Rest" title="Median rest between reps of that part, seconds or m:ss: 2:00-3:00, 90-, -60">
        <QInput k="rest_s" value={g("rest_s")} placeholder="2:00-3:00" width={84} />
      </Fld>
      <Fld label="Part" title="Rep filters must hold for one part of the workout: any part, or only the main part">
        <Seg2 k="part" value={g("part")} a={["", "any"]} b={["main", "main"]} />
      </Fld>
      <Fld label="km" title="Distance: 8-12, 10- or 10 (± tolerance)">
        <QInput k="distance_km" value={g("distance_km")} placeholder="8-12" width={64} />
      </Fld>
      <Fld label="Dates">
        <input type="date" value={g("from")} style={{ width: "128px" }} onChange={(e) => setQuery({ from: (e.target as HTMLInputElement).value })} />
        <span class="muted">–</span>
        <input type="date" value={g("to")} style={{ width: "128px" }} onChange={(e) => setQuery({ to: (e.target as HTMLInputElement).value })} />
      </Fld>
      {active > 0 && <button onClick={() => { const p: Record<string, null> = {}; for (const k of FILTER_KEYS) p[k] = null; setQuery(p); }}>Clear filters</button>}
    </div>
  );
}

// ------------------------------------------------------------------ results
const speed = (r: SearchRow) => (r.sport === "run" || r.sport === "hike" ? (r.speed_ms ? pace(r.speed_ms) + " /km" : "—") : r.speed_ms ? kmh(r.speed_ms) + " km/h" : "—");
const workoutText = (r: SearchRow) => r.rep_count ? (r.exact_key ?? r.signature) : (r.signature ?? r.family_label);

function resultCols(picked: Set<string>, togglePick: (id: string) => void): Col<SearchRow>[] {
  return [
    { key: "pick", label: "", width: 28, sort: (r) => (picked.has(r.id) ? 0 : 1),
      render: (r) => <input type="checkbox" checked={picked.has(r.id)} style={{ height: "auto" }} title="Select for Compare"
        onClick={(e) => { e.stopPropagation(); togglePick(r.id); }} /> },
    { key: "date", label: "Date", render: (r) => <span>{r.date} <span class="muted">{r.start?.slice(11, 16)}</span></span> },
    { key: "workout", label: "Workout", sort: (r) => workoutText(r) ?? "", render: (r) => <span title={r.name ?? ""}><SportDot sport={r.sport} />{workoutText(r)}</span> },
    { key: "rep_count", label: "Reps", num: true, render: (r) => num(r.rep_count) },
    { key: "rep_len", label: "Rep", num: true, sort: (r) => r.rep_dist_m, render: (r) => (r.rep_count ? repLen(r) : "—") },
    { key: "rest_s", label: "Rest", num: true, render: (r) => (r.rest_s ? mmss(r.rest_s) : "—") },
    { key: "dist_km", label: "km", num: true, render: (r) => km(r.dist_km) },
    { key: "moving_s", label: "Moving", num: true, render: (r) => dur(r.moving_s) },
    { key: "speed_ms", label: "Pace / speed", num: true, render: speed },
    { key: "avg_hr", label: "HR", num: true, render: (r) => num(r.avg_hr) },
    { key: "climb_m", label: "Climb", num: true, render: (r) => num(r.climb_m) },
    { key: "place", label: "Place", render: (r) => <span title={r.places.length ? "touches " + r.places.join(", ") : ""}>{r.place ?? "—"}</span> },
    { key: "region", label: "Region" },
    { key: "route", label: "Route", sort: (r) => (r.route ? +r.route.slice(1) : null), render: (r) => <span title={r.route_name ?? ""}>{r.route ?? "—"}</span> },
  ];
}

function Results({ data, error, picked, togglePick }: { data: SearchResult | null; error: string | null; picked: Set<string>; togglePick: (id: string) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  const rows = data?.rows ?? [];
  const anyReps = rows.some((r) => r.rep_count);
  const cols = useMemo(() => resultCols(picked, togglePick).filter((c) => anyReps || !["rep_count", "rep_len", "rest_s"].includes(c.key)), [picked, anyReps]);
  const tracks = useMemo(() => rows.filter((r) => r.poly).map((r) => ({ id: r.id, sport: r.sport, poly: r.poly, width: picked.has(r.id) ? 4 : 2.5 })), [rows, picked]);
  const noGps = rows.length - tracks.length;
  return (
    <div class="split">
      <div class="left">
        {error ? <div class="err pane">{error}</div> :
          <Table cols={cols} rows={rows} rowKey={(r) => r.id} highlight={hover} maxHeight="100%" defaultSort={["date", -1]}
            onRow={(r) => navigate(`/activities/${r.id}`)} onHover={(r) => setHover(r?.id ?? null)}
            empty={data ? "No activities match these filters." : "Loading…"} />}
      </div>
      <div class="right" style={{ width: "38%" }}>
        <TracksMap tracks={tracks} highlight={hover} onHover={setHover} onClick={(id) => navigate(`/activities/${id}`)}
          style={{ flex: 1, minHeight: "300px" }} markers={false} empty={data ? "No GPS tracks in the result" : "Loading…"}>
          {noGps > 0 && tracks.length > 0 && <div class="overlay bl">{noGps} without GPS not shown</div>}
        </TracksMap>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ groups
type GK = "places" | "regions" | "routes" | "workouts";
const GK_KIND: Record<GK, string> = { places: "place", regions: "region", routes: "route", workouts: "workout" };

const sportsCell = (s: Record<string, number>) => (
  <span>{Object.entries(s).map(([k, n]) => <span key={k} style={{ marginRight: "8px" }}><SportDot sport={k} />{n}</span>)}</span>
);

interface FlatNode { path: string; depth: number; node: TreeNode; patch: Record<string, string | null>; label: string; open: boolean; leaf: boolean }

function flatten(tree: TreeNode[], open: Set<string>): FlatNode[] {
  const out: FlatNode[] = [];
  for (const f of tree) {
    const fp = f.key ?? "";
    out.push({ path: fp, depth: 0, node: f, label: f.label ?? f.key ?? "—", open: open.has(fp), leaf: false,
      patch: { family: f.key, signature: null, exact_key: null } });
    if (!open.has(fp)) continue;
    for (const s of f.children ?? []) {
      const sp = fp + " › " + (s.key ?? "");
      out.push({ path: sp, depth: 1, node: s, label: s.key ?? "—", open: open.has(sp), leaf: false,
        patch: { family: f.key, signature: s.key, exact_key: null } });
      if (!open.has(sp)) continue;
      for (const e of s.children ?? []) {
        const label = e.key ? (s.key && e.key.startsWith(s.key + " · ") ? e.key.slice(s.key.length + 3) : e.key) : "(no route / no rest group)";
        out.push({ path: sp + " › " + (e.key ?? ""), depth: 2, node: e, label, open: false, leaf: true,
          patch: { family: f.key, signature: s.key, exact_key: e.key } });
      }
    }
  }
  return out;
}

function Groups({ query, fq, rowsById }: { query: URLSearchParams; fq: string; rowsById: Map<string, SearchRow> }) {
  const gk = ((query.get("gk") as GK) || "places");
  const touched = query.get("touched") === "1";
  const sel = query.get("g");
  const kind = GK_KIND[gk] ?? "place";
  const url = `/api/groups?kind=${kind}${touched && (gk === "places" || gk === "regions") ? "&touched=1" : ""}${fq ? "&" + fq : ""}`;
  const { data, error } = useApi<{ kind: string; groups: any[] }>(url);
  const [hover, setHover] = useState<string | null>(null);
  // open: run/intervals by default, plus the ancestors of the selected node
  const [open, setOpen] = useState<Set<string>>(() => {
    const o = new Set(["run/intervals"]);
    const parts = (sel ?? "").split(" › ");
    for (let i = 1; i < parts.length; i++) o.add(parts.slice(0, i).join(" › "));
    return o;
  });

  const apply = (patch: Record<string, string | null>) => {
    const q = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(patch)) { if (v) q.set(k, v); else q.delete(k); }
    for (const k of ["view", "gk", "g", "touched"]) q.delete(k);
    navigate("/search?" + q.toString());
  };
  const groupPatch = (g: Group): Record<string, string | null> =>
    gk === "places" ? { place: g.key, place_mode: touched ? null : "main" } :
    gk === "regions" ? { region: g.key, region_mode: touched ? null : "main" } : { route: g.key };
  const pick = (key: string) => setQuery({ g: key === sel ? null : key });

  const groups: Group[] = gk !== "workouts" ? (data?.groups ?? []) : [];
  const flat = useMemo(() => (gk === "workouts" && data ? flatten(data.groups as TreeNode[], open) : []), [data, open, gk]);
  const active = hover ?? sel;
  const activeIds: string[] = useMemo(() => {
    if (!active) return [];
    if (gk === "workouts") {
      const n = flat.find((f) => f.path === active)?.node;
      const ids = (x?: TreeNode): string[] => (!x ? [] : x.ids ?? (x.children ?? []).flatMap(ids));
      return ids(n);
    }
    return groups.find((g) => g.key === active)?.ids ?? [];
  }, [active, groups, flat]);
  const members: GLine[] = useMemo(() => activeIds.map((id) => rowsById.get(id)).filter((r): r is SearchRow => !!r?.poly)
    .map((r) => ({ id: r.id, key: active!, sport: r.sport, poly: r.poly })), [activeIds, rowsById]);

  const selGroup = gk === "workouts" ? flat.find((f) => f.path === sel) : groups.find((g) => g.key === sel);
  const selIds = sel ? (gk === "workouts" ? activeIdsOf(selGroup as FlatNode | undefined) : (selGroup as Group | undefined)?.ids ?? []) : [];
  const fitSel = useMemo(() => {
    const rs = selIds.map((id) => rowsById.get(id)?.bbox).filter((b): b is [number, number, number, number] => !!b);
    if (!rs.length) {
      const g = selGroup as Group | undefined;
      return g?.lat !== undefined && g?.lon !== undefined ? [g.lon - 0.01, g.lat - 0.005, g.lon + 0.01, g.lat + 0.005] as [number, number, number, number] : null;
    }
    return rs.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);
  }, [sel, data, rowsById]);

  let table: any;
  if (gk === "workouts") {
    const toggle = (p: string) => { const s = new Set(open); s.has(p) ? s.delete(p) : s.add(p); setOpen(s); };
    table = (
      <Table rows={flat} rowKey={(f) => f.path} selected={sel} highlight={hover} maxHeight="100%" follow
        onRow={(f) => (f.path === sel ? apply(f.patch) : pick(f.path))} onHover={(f) => setHover(f?.path ?? null)}
        empty={data ? "No workouts match." : "Loading…"} cols={[
          { key: "label", label: "Family › signature › same workout", sort: () => null, render: (f) => (
            <span style={{ paddingLeft: `${f.depth * 20}px`, display: "inline-flex", alignItems: "center", gap: "6px" }}>
              {!f.leaf ? <button class="caret" onClick={(e) => { e.stopPropagation(); toggle(f.path); }}>{f.open ? "▾" : "▸"}</button> : <span class="caret" />}
              <span style={f.depth === 0 ? { fontWeight: 600 } : undefined}>{f.label}</span>
            </span>) },
          { key: "go", label: "", sort: () => null, render: (f) => <a href="#" onClick={(e) => { e.preventDefault(); e.stopPropagation(); apply(f.patch); }}>list →</a> },
          { key: "n", label: "Sessions", num: true, sort: () => null, render: (f) => f.node.n },
          { key: "first", label: "First", sort: () => null, render: (f) => f.node.first ?? "—" },
          { key: "last", label: "Last", sort: () => null, render: (f) => f.node.last ?? "—" },
          { key: "places", label: "Main places", sort: () => null, render: (f) => <span class="muted">{Object.entries(f.node.places).map(([k, n]) => `${k} ${n}`).join(", ")}</span> },
        ]} />
    );
  } else {
    const cols: Col<Group>[] = [
      { key: "label", label: gk === "places" ? "Place" : gk === "regions" ? "Region" : "Route", render: (g) => (
        <span title={g.label}>{gk === "routes" ? <><span class="mono">{g.key}</span> {g.label}</> : g.label}</span>) },
    ];
    cols.push({ key: "go", label: "", sort: () => null, render: (g) => <a href="#" onClick={(e) => { e.preventDefault(); e.stopPropagation(); apply(groupPatch(g)); }}>list →</a> });
    if (gk !== "routes") cols.push({ key: "kind", label: "Kind", render: (g) => <span class="muted">{g.kind ?? "—"}</span> });
    cols.push({ key: "n", label: touched ? "Touched" : "Sessions", num: true });
    if (gk === "places" || gk === "regions") cols.push({ key: touched ? "n_main" : "n_touched", label: touched ? "Main" : "Touched", num: true, render: (g) => num(touched ? g.n_main : g.n_touched) });
    if (gk === "routes") cols.push(
      { key: "dist_km", label: "km", num: true, render: (g) => km(g.dist_km) },
      { key: "climb_m", label: "Climb", num: true, render: (g) => num(g.climb_m) },
    );
    cols.push(
      { key: "sports", label: "Sports", sort: (g) => Object.keys(g.sports)[0], render: (g) => sportsCell(g.sports) },
      { key: "first", label: "First" }, { key: "last", label: "Last" },
    );
    table = (
      <Table rows={groups} rowKey={(g) => g.key} cols={cols} selected={sel} highlight={hover} maxHeight="100%" follow defaultSort={["n", -1]}
        onRow={(g) => (g.key === sel ? apply(groupPatch(g)) : pick(g.key))} onHover={(g) => setHover(g?.key ?? null)}
        empty={data ? "No groups match." : "Loading…"} />
    );
  }

  const points = gk === "places" ? groups.filter((g) => g.lat !== undefined && g.lat !== null).map((g) => ({ key: g.key, lat: g.lat!, lon: g.lon!, n: g.n,
    color: g.src === "named" ? "#2f5fd0" : "#8a9199" })) : [];
  const shapes = (gk === "places" || gk === "regions") ? groups.filter((g) => g.shape?.circle || g.shape?.poly).map((g) => ({ key: g.key, ...g.shape! })) : [];
  const lines: GLine[] = gk === "routes" ? groups.filter((g) => g.poly).map((g) => ({ id: g.key, key: g.key, sport: Object.keys(g.sports)[0] ?? "other", poly: g.poly })) : [];

  const n = gk === "workouts" ? (data?.groups ?? []).length : groups.length;
  return (
    <>
      <div class="toolbar sub">
        <div class="seg">
          {(["places", "regions", "routes", "workouts"] as GK[]).map((k) => (
            <button key={k} class={gk === k ? "on" : ""} onClick={() => setQuery({ gk: k === "places" ? null : k, g: null })}>{k[0].toUpperCase() + k.slice(1)}</button>
          ))}
        </div>
        {(gk === "places" || gk === "regions") && <Seg2 k="touched" value={touched ? "1" : ""} a={["", "Main " + (gk === "places" ? "place" : "region")]} b={["1", "Every touched"]}
          title="Main = ≥ 40 % of the time inside (one per activity); touched = ≥ 5 % or 3 min (an activity can count in several)" />}
        <span class="count">{error ?? (data ? `${n} ${gk === "workouts" ? "families" : gk}` : "Loading…")}</span>
        <span class="muted">Click a row to show it on the map, click again (or “list →”) to list its activities{gk === "places" ? "; grey = start clusters, blue = named places" : ""}</span>
      </div>
      <div class="split">
        <div class="left">{table}</div>
        <div class="right" style={{ width: "38%" }}>
          <GroupsMap points={points} shapes={shapes} lines={lines} members={members} highlight={hover} selected={sel} fit={sel ? fitSel : null}
            onHover={(k) => setHover(k)} onClick={(k) => pick(k)} style={{ flex: 1, minHeight: "300px" }}
            empty={gk === "workouts" ? "Hover or select a group to see its tracks" : data ? "No shapes" : "Loading…"} />
        </div>
      </div>
    </>
  );
}

function activeIdsOf(f: FlatNode | undefined): string[] {
  const ids = (x?: TreeNode): string[] => (!x ? [] : x.ids ?? (x.children ?? []).flatMap(ids));
  return ids(f?.node);
}

// ------------------------------------------------------------------ page
export function SearchPage() {
  const { query } = useLocation();
  const view = query.get("view") === "groups" ? "groups" : "results";
  const sport = query.get("sport") || "";
  const fq = filterQuery(query);
  const opts = useApi<SearchOptions>("/api/search/options").data;
  const res = useApi<SearchResult>("/api/search" + (fq ? "?" + fq : ""));
  const picked = useMemo(() => new Set((query.get("pick") || "").split(",").filter(Boolean)), [query.get("pick")]);
  const togglePick = (id: string) => {
    const s = new Set(picked); s.has(id) ? s.delete(id) : s.add(id);
    setQuery({ pick: [...s].join(",") });
  };
  const rowsById = useMemo(() => new Map((res.data?.rows ?? []).map((r) => [r.id, r])), [res.data]);
  useEffect(() => { document.title = "Find & group"; }, []);
  const d = res.data;
  const sports = opts?.sports ?? [];

  return (
    <main class="full">
      <div class="toolbar">
        <h1>Find & group</h1>
        <div class="seg">
          <button class={view === "results" ? "on" : ""} onClick={() => setQuery({ view: null })}>Activities</button>
          <button class={view === "groups" ? "on" : ""} onClick={() => setQuery({ view: "groups" })}>Groups</button>
        </div>
        <QInput k="q" value={query.get("q") || ""} placeholder="Words: name, place, route, 6×1000…" width={230} />
        <div class="chips">
          <button class={"chip" + (!sport ? " on" : "")} onClick={() => setQuery({ sport: null })}>All</button>
          {sports.map((s) => (
            <button key={s.id} class={"chip" + (sport === s.id ? " on" : "")} onClick={() => setQuery({ sport: sport === s.id ? null : s.id })}>
              <span class="dot" style={{ background: sportColor(s.id) }} />{SPORT_LABEL[s.id] ?? s.id}
            </button>
          ))}
        </div>
        <span class="count">{res.error ?? (d ? `${d.n} activities, ${Math.round(d.km).toLocaleString("en-GB")} km` : "…")}</span>
        <div class="tools">
          {picked.size > 0 && <button onClick={() => setQuery({ pick: null })} title="Clear the selection">Clear {picked.size} selected</button>}
          {d && picked.size === 0 && d.n >= 2 && d.n <= 12 && view === "results" &&
            <a class="btn" href={compareUrl(d.rows.map((r) => r.id))} title="Compare every activity in the result">Compare all {d.n}</a>}
          <a class={"btn" + (picked.size < 2 ? " disabled" : "")} href={picked.size >= 2 ? compareUrl([...picked]) : undefined}
            title={picked.size < 2 ? "Tick at least two rows" : "Open the selected activities in Compare"}>Compare selected{picked.size ? ` (${picked.size})` : ""}</a>
        </div>
      </div>
      <FilterBar query={query} opts={opts} />
      {view === "results" ? <Results data={d} error={res.error} picked={picked} togglePick={togglePick} />
        : <Groups query={query} fq={fq} rowsById={rowsById} />}
    </main>
  );
}

export const searchUrl = (patch: Record<string, string | number | null | undefined>) => "/search" + qs(patch);
