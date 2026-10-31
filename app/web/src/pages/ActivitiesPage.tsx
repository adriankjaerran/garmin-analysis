// Activities: every activity, filtered. Simple filters up front (words, sport, period / dates), an Advanced panel
// (place / region, route, workout family / signature / same workout, reps, rep length, rest, part, km). Results =
// sortable table + map of all result tracks; selecting a row zooms the map to it and shows a summary (Open, Day,
// Compare, Closest), clicking it again opens the activity. Groups tab: the places, regions, routes and workout tree
// that the current filters leave. Tick rows to compare them. Every control lives in the URL; the old /search
// (Find & group) URLs redirect here with the same filters.
import { useEffect, useMemo, useState } from "preact/hooks";
import { useApi, type Activity, type Meta } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { SPORTS, SPORT_LABEL, addDays, dur, isoLocal, km, num, pace, pace100, kmh, sportColor } from "../lib/format";
import { FILTER_KEYS, compareUrl, coreBBox, filterQuery, placeCount, repLen, mmss, type Group, type SearchOptions, type SearchResult, type SearchRow, type TreeNode } from "../lib/search";
import { Table, type Col } from "../components/Table";
import { TracksMap } from "../components/TracksMap";
import { GroupsMap, type GLine } from "../components/GroupsMap";
import { ActivityKV, SportDot } from "../components/ActivityBits";

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

/** date filter: an empty text box ("from" / "to") until focused, so an unset filter doesn't show today's date */
function DateIn({ k, value, placeholder }: { k: string; value: string; placeholder: string }) {
  const [edit, setEdit] = useState(false);
  const props: any = { type: value || edit ? "date" : "text" };
  return <input {...props} value={value} placeholder={placeholder} style={{ width: "128px" }}
    onFocus={() => setEdit(true)} onBlur={() => setEdit(false)}
    onChange={(e) => setQuery({ [k]: (e.target as HTMLInputElement).value })} />;
}

const Fld = ({ label, children, title }: { label: string; children: any; title?: string }) => (
  <label class="fld" title={title}><span>{label}</span>{children}</label>
);

// ------------------------------------------------------------------ simple filters (always visible)
/** advanced filter keys (everything but words, sport and dates) */
const ADV_KEYS = FILTER_KEYS.filter((k) => !["q", "sport", "from", "to"].includes(k));

/** period presets -> from / to */
function periods(meta: Meta | null) {
  const today = isoLocal();
  const out: { id: string; label: string; from: string; to: string }[] = [
    { id: "7d", label: "Last 7 days", from: addDays(today, -6), to: "" },
    { id: "30d", label: "Last 30 days", from: addDays(today, -29), to: "" },
    { id: "90d", label: "Last 90 days", from: addDays(today, -89), to: "" },
    { id: "12m", label: "Last 12 months", from: addDays(today, -364), to: "" },
  ];
  for (const y of meta?.years ?? []) out.push({ id: y, label: y, from: `${y}-01-01`, to: `${y}-12-31` });
  return out;
}

/** one-line label of an active advanced filter (for the chips when the panel is closed) */
function advLabel(k: string, v: string, opts: SearchOptions | null, query: URLSearchParams): string {
  const name = (list: { id: string; name?: string; label?: string }[] | undefined) => list?.find((o) => o.id === v)?.name ?? v;
  switch (k) {
    case "place": return `Place: ${name(opts?.places)}${query.get("place_mode") === "main" ? " (main)" : ""}`;
    case "region": return `Region: ${name(opts?.regions)}${query.get("region_mode") === "main" ? " (main)" : ""}`;
    case "route": return `Route: ${v}`;
    case "family": return opts?.families.find((f) => f.id === v)?.label ?? v;
    case "category": return `Category: ${v.replace("_", " ")}`;
    case "signature": return v;
    case "exact_key": return v;
    case "rep_count": return `${v} reps`;
    case "rep_len": return `reps ${v}${query.get("tol") ? ` ± ${Math.round(+query.get("tol")! * 100)} %` : ""}`;
    case "rest_s": return `rest ${v}`;
    case "part": return "main part";
    case "distance_km": return `${v} km`;
    default: return `${k} ${v}`;
  }
}

function SimpleBar({ query, opts, meta, advOpen }: { query: URLSearchParams; opts: SearchOptions | null; meta: Meta | null; advOpen: boolean }) {
  const g = (k: string) => query.get(k) || "";
  const sport = g("sport");
  const sports = useMemo(() => [...(opts?.sports ?? [])].sort((a, b) => SPORTS.indexOf(a.id as any) - SPORTS.indexOf(b.id as any)), [opts]);
  const per = periods(meta);
  const from = g("from"), to = g("to");
  const cur = per.find((p) => p.from === from && p.to === to)?.id ?? (from || to ? "custom" : "");
  // the chips stand for filters set in the panel; mode / tolerance keys are shown inside their filter's chip
  const shown = ADV_KEYS.filter((k) => query.get(k) && !["place_mode", "region_mode", "tol"].includes(k));
  const nAdv = shown.length;
  const anyFilter = FILTER_KEYS.some((k) => query.get(k));
  const drop = (k: string) => setQuery(k === "place" ? { place: null, place_mode: null } : k === "region" ? { region: null, region_mode: null } :
    k === "rep_len" ? { rep_len: null, tol: null } : { [k]: null });
  return (
    <div class="fsimple">
      <QInput k="q" value={g("q")} placeholder="Search: name, place, route, 6×1000…" width={250} />
      <div class="chips">
        <button class={"chip" + (!sport ? " on" : "")} onClick={() => setQuery({ sport: null })}>All</button>
        {sports.map((s) => (
          <button key={s.id} class={"chip" + (sport === s.id ? " on" : "")} onClick={() => setQuery({ sport: sport === s.id ? null : s.id })} title={`${s.n} activities`}>
            <span class="dot" style={{ background: sportColor(s.id) }} />{s.label ?? SPORT_LABEL[s.id] ?? s.id}
          </button>
        ))}
      </div>
      <span class="sep" />
      <select value={cur} title="Period" onChange={(e) => {
        const v = (e.target as HTMLSelectElement).value, p = per.find((x) => x.id === v);
        if (v === "custom") return;
        setQuery({ from: p?.from ?? null, to: p?.to ?? null });
      }}>
        <option value="">Any time</option>
        {per.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        {cur === "custom" && <option value="custom">Custom dates</option>}
      </select>
      <DateIn k="from" value={from} placeholder="from" />
      <span class="muted">–</span>
      <DateIn k="to" value={to} placeholder="to" />
      <span class="sep" />
      <button class={"adv-toggle" + (advOpen ? " on" : "")} onClick={() => setQuery({ adv: advOpen ? null : "1" })}
        title="Place, region, route, workout, reps, rest, distance">
        Advanced{nAdv ? ` (${nAdv})` : ""} {advOpen ? "▴" : "▾"}
      </button>
      {!advOpen && shown.map((k) => (
        <button key={k} class="chip on" onClick={() => drop(k)} title="Remove this filter">{advLabel(k, query.get(k)!, opts, query)} ×</button>
      ))}
      {anyFilter && <button class="link" onClick={() => { const p: Record<string, null> = {}; for (const k of FILTER_KEYS) p[k] = null; setQuery(p); }}>Clear all</button>}
    </div>
  );
}

// ------------------------------------------------------------------ advanced panel
function AdvancedPanel({ query, opts }: { query: URLSearchParams; opts: SearchOptions | null }) {
  const g = (k: string) => query.get(k) || "";
  const sig = g("signature");
  const exact = (opts?.exact ?? []).filter((e) => !sig || sig.startsWith("~") ? e.id.includes(sig.replace(/^~/, "")) : e.id.startsWith(sig + " ·"));
  const active = ADV_KEYS.filter((k) => query.get(k)).length;
  return (
    <div class="fbar">
      <Fld label="Place" title="Touched = starts, ends or spends ≥ 5 % of the time (or 3 min) there; main = ≥ 40 % of the time (one per activity). Counts: main · touched">
        <Sel k="place" value={g("place")} all="Any" width={140}
          opts={(opts?.places ?? []).map((p) => ({ id: p.id, label: `${p.name ?? p.id} (${placeCount(p.n_main, p.n)})` }))} />
        <Seg2 k="place_mode" value={g("place_mode")} a={["", "touched"]} b={["main", "main"]} />
      </Fld>
      <Fld label="Region" title="Marka / Østmarka / Bymarka polygons, else the Garmin municipality">
        <Sel k="region" value={g("region")} all="Any" width={130}
          opts={(opts?.regions ?? []).map((p) => ({ id: p.id, label: `${p.name ?? p.id} (${placeCount(p.n_main, p.n)})` }))} />
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
      {active > 0 && <button class="link" onClick={() => { const p: Record<string, null> = {}; for (const k of ADV_KEYS) p[k] = null; setQuery(p); }}>Clear advanced</button>}
    </div>
  );
}

// ------------------------------------------------------------------ results
const usesPace = (s: string) => s === "run" || s === "hike";
/** session or rep speed in the unit of the sport: run / hike min/km, swim /100 m, else km/h */
function speedCell(sport: string, ms: number | null | undefined) {
  if (!ms) return "—";
  if (usesPace(sport)) return pace(ms);
  if (sport === "swim") return <>{pace100(ms)}<span class="muted"> /100</span></>;
  return <>{kmh(ms)}<span class="muted"> km/h</span></>;
}
const speedTitle = "Moving pace (min/km) for runs and hikes, /100 m for swims, else km/h";
/** the signature ("6×1000", "3×1800 + 2×900", "Easy run 8–12 km"); same-workout label, reps and name in the tooltip */
const workoutText = (r: SearchRow) => r.signature ?? r.family_label;
const workoutTip = (r: SearchRow) => [r.exact_label ?? r.exact_key, r.rep_count ? `main reps ${r.rep_count} × ${repLen(r)} (measured median)` : "",
  r.name, r.sport_label].filter(Boolean).join(" · ");
const placeText = (r: SearchRow) => r.place_name ?? r.place;

function resultCols(picked: Set<string>, togglePick: (id: string) => void): Col<SearchRow>[] {
  return [
    { key: "pick", label: "", width: 28, sort: (r) => (picked.has(r.id) ? 0 : 1),
      render: (r) => <input type="checkbox" checked={picked.has(r.id)} style={{ height: "auto" }} title="Select for Compare"
        onClick={(e) => { e.stopPropagation(); togglePick(r.id); }} /> },
    { key: "date", label: "Date", render: (r) => <span>{r.date} <span class="muted">{r.start?.slice(11, 16)}</span></span> },
    { key: "workout", label: "Workout", sort: (r) => workoutText(r) ?? "",
      render: (r) => <span class="clip" style={{ maxWidth: "200px" }} title={workoutTip(r)}><SportDot sport={r.sport} />{workoutText(r)}</span> },
    { key: "rep_speed_ms", label: "Rep pace", num: true, title: "Mean pace (runs) / speed of the main reps; hover for the mean rep time",
      sort: (r) => (r.rep_speed_ms ? (usesPace(r.sport) ? 1000 / r.rep_speed_ms : -r.rep_speed_ms) : null),
      render: (r) => (r.rep_count ? <span title={r.rep_time_s ? `mean rep time ${mmss(r.rep_time_s)}` : ""}>{speedCell(r.sport, r.rep_speed_ms)}</span> : "") },
    { key: "rest_s", label: "Rest", num: true, title: "This activity's median rest between reps (the same-workout group's median in the tooltip; “rest ~2:30” in the workout name is that group's bin)",
      render: (r) => (r.rest_s ? <span title={r.rest_group_s ? `this activity ${mmss(r.rest_s)} · its same-workout group ${mmss(r.rest_group_s)}` : ""}>{mmss(r.rest_s)}</span> : "") },
    { key: "dist_km", label: "km", num: true, render: (r) => km(r.dist_km) },
    { key: "moving_s", label: "Time", num: true, title: "Moving time", render: (r) => dur(r.moving_s) },
    { key: "speed_ms", label: "Pace", num: true, title: speedTitle,
      sort: (r) => (r.speed_ms ? (usesPace(r.sport) ? 1000 / r.speed_ms : -r.speed_ms) : null), render: (r) => speedCell(r.sport, r.speed_ms) },
    { key: "avg_hr", label: "HR", num: true, title: "Average HR of the whole activity", render: (r) => num(r.avg_hr) },
    { key: "climb_m", label: "Climb", num: true, render: (r) => num(r.climb_m) },
    { key: "place", label: "Place", sort: (r) => placeText(r) ?? "",
      render: (r) => <span class="clip" style={{ maxWidth: "120px" }} title={[(r.place_names ?? r.places).length ? "touches " + (r.place_names ?? r.places).join(", ") : "", r.region ? "region " + r.region : ""].filter(Boolean).join(" · ")}>{placeText(r) ?? "—"}</span> },
    { key: "route", label: "Route", sort: (r) => (r.route ? +r.route.slice(1) : null), render: (r) => <span title={r.route_name ?? ""}>{r.route ?? "—"}</span> },
  ];
}

/** selected row: summary + actions under the map (the map zooms to its track) */
function Preview({ id, row }: { id: string; row: SearchRow | undefined }) {
  const list = useApi<{ n: number; activities: Activity[] }>("/api/activities").data;   // memoised app rows (fit / track flags, load…)
  const a = list?.activities.find((x) => x.id === id);
  return (
    <div class="pane preview">
      <div class="head">
        <h2 class="clip" style={{ maxWidth: "calc(100% - 40px)" }} title={a?.name ?? row?.name ?? ""}>{a?.name ?? row?.name ?? id}</h2>
        <div class="tools"><button class="x" title="Clear the selection (back to all results on the map)" onClick={() => setQuery({ sel: null })}>×</button></div>
      </div>
      {a ? <ActivityKV a={a} /> : <div class="empty">Loading…</div>}
      <div class="head" style={{ marginTop: "12px" }}>
        <a class="btn" href={`/activities/${id}`}>Open activity</a>
        {a && <a class="btn" href={`/day/${a.date}`}>Day view</a>}
        {a?.fit && <a class="btn" href={`/compare?suggest=${id}`} title="Compare with similar sessions (same workout / route)">Compare</a>}
        <a class="btn" href={`/activities/${id}?go=closest`} title="Closest activities (same workout, route, place)">Closest</a>
      </div>
    </div>
  );
}

function Results({ data, error, picked, togglePick, sel }: { data: SearchResult | null; error: string | null; picked: Set<string>; togglePick: (id: string) => void; sel: string | null }) {
  const [hover, setHover] = useState<string | null>(null);
  const rows = data?.rows ?? [];
  const anyReps = rows.some((r) => r.rep_count);
  const cols = useMemo(() => resultCols(picked, togglePick).filter((c) => anyReps || !["rep_speed_ms", "rest_s"].includes(c.key)), [picked, anyReps]);
  const selRow = sel ? rows.find((r) => r.id === sel) : undefined;
  const tracks = useMemo(() => rows.filter((r) => r.poly).map((r) => ({ id: r.id, sport: r.sport, poly: r.poly,
    width: r.id === sel ? 4.5 : picked.has(r.id) ? 4 : 2.5,
    // a selected row: the others become grey context
    ...(sel && r.id !== sel && !picked.has(r.id) ? { color: "#8a9199", width: 1.5 } : {}) })), [rows, picked, sel]);
  const noGps = rows.length - tracks.length;
  // fit the home cluster, not the whole world (trips abroad): "show all" widens it; a selected row: its track
  const [all, setAll] = useState(false);
  const core = useMemo(() => {
    const boxes = rows.filter((r) => r.poly && r.bbox).map((r) => r.bbox!);
    return { ...coreBBox(boxes), whole: boxes.length ? coreBBox(boxes, 1e6).fit : null };
  }, [rows]);
  const fit = selRow?.bbox && selRow.poly ? selRow.bbox : all ? core.whole : core.fit;
  return (
    <div class="split">
      <div class="left">
        {error ? <div class="err pane">{error}</div> :
          <Table cols={cols} rows={rows} rowKey={(r) => r.id} highlight={hover} selected={sel} follow maxHeight="100%" defaultSort={["date", -1]} stickyCols={2}
            onRow={(r) => (r.id === sel ? navigate(`/activities/${r.id}`) : setQuery({ sel: r.id }))} onHover={(r) => setHover(r?.id ?? null)}
            empty={data ? "No activities match these filters." : "Loading…"} />}
      </div>
      <div class="right" style={{ width: "30%", minWidth: "400px" }}>
        <TracksMap tracks={tracks} highlight={hover ?? sel} onHover={setHover}
          onClick={(id) => (id === sel ? navigate(`/activities/${id}`) : setQuery({ sel: id }))}
          style={{ flex: 1, minHeight: "300px" }} markers={!!sel} empty={data ? "No GPS tracks in the result" : "Loading…"}
          fit={fit} defaultBasemap="grey">
          {!sel && tracks.length > 0 && (noGps > 0 || core.out > 0) && <div class="overlay bl">
            {noGps > 0 && `${noGps} without GPS not shown`}
            {noGps > 0 && core.out > 0 ? " · " : ""}
            {core.out > 0 && (all ? <a href="#" onClick={(e) => { e.preventDefault(); setAll(false); }}>zoom to the main area</a>
              : <>{core.out} elsewhere · <a href="#" onClick={(e) => { e.preventDefault(); setAll(true); }}>show all</a></>)}
          </div>}
          {sel && selRow && !selRow.poly && <div class="msg">No GPS for the selected activity</div>}
        </TracksMap>
        {sel ? <Preview key={sel} id={sel} row={selRow} /> :
          <div class="pane hint">Click a row to preview it here (the map zooms to it); click it again to open the activity. Tick rows to compare them.</div>}
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
    navigate("/activities?" + q.toString());
  };
  const groupPatch = (g: Group): Record<string, string | null> =>
    gk === "places" ? { place: g.key, place_mode: touched ? null : "main" } :
    gk === "regions" ? { region: g.key, region_mode: touched ? null : "main" } : { route: g.key };
  const pick = (key: string) => setQuery({ g: key === sel ? null : key });

  const groups: Group[] = useMemo(() => (gk !== "workouts" ? (data?.groups ?? []) : []), [data, gk]);
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
      return { fit: g?.lat !== undefined && g?.lon !== undefined ? [g.lon - 0.01, g.lat - 0.005, g.lon + 0.01, g.lat + 0.005] as [number, number, number, number] : null, out: 0 };
    }
    return coreBBox(rs);   // members abroad / in other towns: fit the main cluster, say how many are elsewhere
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
          { key: "n", label: "Activities", num: true, sort: () => null, render: (f) => f.node.n },
          { key: "first", label: "First", sort: () => null, render: (f) => f.node.first ?? "—" },
          { key: "last", label: "Last", sort: () => null, render: (f) => f.node.last ?? "—" },
          { key: "places", label: "Main places", sort: () => null, render: (f) => <span class="muted">{Object.entries(f.node.places).map(([k, n]) => `${k} ${n}`).join(", ")}</span> },
        ]} />
    );
  } else {
    const cols: Col<Group>[] = [
      { key: "label", label: gk === "places" ? "Place" : gk === "regions" ? "Region" : "Route", render: (g) => (
        <span title={g.label + (gk === "places" && g.n_start ? ` · ${g.n_start} start here` : "")}>{gk === "routes" ? <><span class="mono">{g.key}</span> {g.label}</> : (g.name ?? g.label)}</span>) },
    ];
    cols.push({ key: "go", label: "", sort: () => null, render: (g) => <a href="#" onClick={(e) => { e.preventDefault(); e.stopPropagation(); apply(groupPatch(g)); }}>list →</a> });
    if (gk !== "routes") cols.push({ key: "kind", label: "Kind", render: (g) => <span class="muted">{g.kind ?? "—"}</span> });
    if (gk === "places" || gk === "regions") cols.push({ key: "n", label: "Activities", num: true,
      title: "main place · every touched (an activity counts in one main place, but can touch several)",
      sort: (g) => (touched ? g.n_touched ?? g.n : g.n_main ?? g.n),
      render: (g) => placeCount(g.n_main ?? g.n, g.n_touched) });
    else cols.push({ key: "n", label: "Activities", num: true });
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

  const points = useMemo(() => gk === "places" ? groups.filter((g) => g.lat !== undefined && g.lat !== null).map((g) => ({ key: g.key, lat: g.lat!, lon: g.lon!, n: g.n,
    color: g.src === "named" ? "#2f5fd0" : "#8a9199" })) : [], [groups, gk]);
  const shapes = useMemo(() => (gk === "places" || gk === "regions") ? groups.filter((g) => g.shape?.circle || g.shape?.poly).map((g) => ({ key: g.key, ...g.shape! })) : [], [groups, gk]);
  const lines: GLine[] = useMemo(() => gk === "routes" ? groups.filter((g) => g.poly).map((g) => ({ id: g.key, key: g.key, sport: Object.keys(g.sports)[0] ?? "other", poly: g.poly })) : [], [groups, gk]);

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
          <GroupsMap points={points} shapes={shapes} lines={lines} members={members} highlight={hover} selected={sel} fit={sel ? fitSel.fit : null} fitOut={sel ? fitSel.out : 0}
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
export function ActivitiesPage() {
  const { query } = useLocation();
  const view = query.get("view") === "groups" ? "groups" : "results";
  const fq = filterQuery(query);
  const meta = useApi<Meta>("/api/meta").data;
  const opts = useApi<SearchOptions>("/api/search/options").data;
  const res = useApi<SearchResult>("/api/search" + (fq ? "?" + fq : ""));
  const picked = useMemo(() => new Set((query.get("pick") || "").split(",").filter(Boolean)), [query.get("pick")]);
  const togglePick = (id: string) => {
    const s = new Set(picked); s.has(id) ? s.delete(id) : s.add(id);
    setQuery({ pick: [...s].join(",") });
  };
  const rowsById = useMemo(() => new Map((res.data?.rows ?? []).map((r) => [r.id, r])), [res.data]);
  // old Activities-list URLs: ?year=2025 -> from / to
  const year = query.get("year");
  useEffect(() => { if (year) setQuery({ year: null, from: `${year}-01-01`, to: `${year}-12-31` }); }, [year]);
  useEffect(() => { document.title = "Activities"; }, []);
  const d = res.data;
  // the advanced panel is open when asked for (adv=1); its filters show as chips in the simple bar otherwise
  const advOpen = query.get("adv") === "1";
  const sel = view === "results" ? query.get("sel") : null;

  return (
    <main class="full">
      <div class="toolbar">
        <h1>Activities</h1>
        <div class="seg">
          <button class={view === "results" ? "on" : ""} onClick={() => setQuery({ view: null })}>List</button>
          <button class={view === "groups" ? "on" : ""} onClick={() => setQuery({ view: "groups", sel: null })}
            title="Places, regions, routes and the workout tree left by the current filters">Groups</button>
        </div>
        <span class="count">{res.error ?? (d ? `${d.n} activities, ${Math.round(d.km).toLocaleString("en-GB")} km` : "…")}</span>
        <div class="tools">
          {picked.size > 0 && <button onClick={() => setQuery({ pick: null })} title="Clear the ticked rows">Clear {picked.size} ticked</button>}
          {d && picked.size === 0 && d.n >= 2 && d.n <= 12 && view === "results" &&
            <a class="btn" href={compareUrl(d.rows.map((r) => r.id))} title="Compare every activity in the result">Compare all {d.n}</a>}
          <a class={"btn" + (picked.size < 2 ? " disabled" : "")} href={picked.size >= 2 ? compareUrl([...picked]) : undefined}
            title={picked.size < 2 ? "Tick at least two rows" : "Open the ticked activities in Compare"}>Compare ticked{picked.size ? ` (${picked.size})` : ""}</a>
        </div>
      </div>
      <SimpleBar query={query} opts={opts} meta={meta} advOpen={advOpen} />
      {advOpen && <AdvancedPanel query={query} opts={opts} />}
      {view === "results" ? <Results data={d} error={res.error} picked={picked} togglePick={togglePick} sel={sel} />
        : <Groups query={query} fq={fq} rowsById={rowsById} />}
    </main>
  );
}
