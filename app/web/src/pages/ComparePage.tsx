// Compare one session with one or many: ghost race along the route, moving / elapsed time, rep-aligned
// intervals (breaks squeezed to a fixed width) and break-aligned recoveries. Engine: scripts/compare.py
// via /api/compare; comparison sets via /api/compare/suggest. Everything is in the URL:
// /compare?ids=a,b,c&mode=distance|moving|elapsed|intervals|breaks&ref=first|median&focus=<id>
//   &metric=pace|speed&delta=moving|elapsed&rep_axis=absolute|normalized&gap=60&rgap=30&match=auto|index
// /compare?suggest=<id> picks the best suggested set for that activity and redirects.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useApi, qs, type Activity } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { SPORT_LABEL, dur, num, km, stypeLabel, typeLabel } from "../lib/format";
import { Table, type Col } from "../components/Table";
import { SportDot } from "../components/ActivityBits";
import { CompareCharts, type CPanel } from "../components/CompareCharts";
import { CompareMap } from "../components/CompareMap";
import {
  MODES, buildGrid, interp, stats, colorsFor, sgn, kmhToPace, xLabel, alignedTicks, spans as spansOf, posAt, FOCUS_MANY, slotName, repGap,
  type Mode, type CmpResult, type CmpSession, type Suggest, type SuggestSet,
} from "../lib/compare";
import "./compare.css";

type N = number | null;
const MAX_IDS = 40;

function parseIds(s: string): string[] {
  return Array.from(new Set((s.match(/\d{6,}/g) ?? [])));
}

/** centred moving mean over +-k grid points (nulls skipped, null stays null) */
function smoothN(a: N[], k: number): N[] {
  return a.map((v, i) => {
    if (v === null) return null;
    let s = 0, n = 0;
    for (let j = Math.max(0, i - k); j <= Math.min(a.length - 1, i + k); j++) if (a[j] !== null) { s += a[j]!; n++; }
    return s / n;
  });
}

/** short workout text for chips and tables: "6×1000", "Ski easy" (workout_label without its route part) */
const shortWhat = (a: Activity | null | undefined) =>
  (a ? a.sig || a.workout_label?.split(" · ")[0] || stypeLabel(a.stype) || a.type_label || typeLabel(a.type) : "");

/** best value per column (min or max), ignoring nulls */
function bestOf<T>(rows: T[], get: (r: T) => N | undefined, dir: "min" | "max" | null): N {
  if (!dir) return null;
  let b: N = null, n = 0;
  for (const r of rows) {
    const v = get(r);
    if (v === null || v === undefined) continue;
    n++;
    if (b === null || (dir === "min" ? v < b : v > b)) b = v;
  }
  return n >= 2 ? b : null;   // nothing to compare with: no "best"
}
const B = ({ v, best, children }: { v: N | undefined; best: N; children: any }) =>
  <span class={v !== null && v !== undefined && best !== null && v === best ? "best" : ""}>{children}</span>;

function pickSet(s: Suggest): SuggestSet | null {
  const by = (k: string) => s.sets.find((x) => x.key === k);
  if (s.mode === "intervals") return by("exact") ?? by("sig_place") ?? by("sig") ?? s.sets[0] ?? null;
  if (s.mode === "distance") return by("route") ?? by("exact") ?? s.sets[0] ?? null;
  return by("exact") ?? s.sets[0] ?? null;
}

// ---------------------------------------------------------------- landing (no ids)
function Landing() {
  const acts = useApi<{ activities: Activity[] }>("/api/activities").data;
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const all = (acts?.activities ?? []).filter((a) => a.fit && (words.length || a.sport === "run" || a.sport === "ski"));
    const hay = (a: Activity) => [a.date, a.name, shortWhat(a), a.place_name, a.place, a.route, a.route_name, a.type_label, SPORT_LABEL[a.sport]].join(" ").toLowerCase();
    return (words.length ? all.filter((a) => { const h = hay(a); return words.every((w) => h.includes(w)); }) : all).slice(0, words.length ? 200 : 40);
  }, [acts, q]);
  return (
    <section>
      <div class="head"><h2>Pick a session</h2>
        <input type="search" placeholder="Filter: 6×1000, Bislett, R3, 2025…" value={q} style={{ width: "260px" }}
          onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
        <span class="sub">{q ? `${rows.length} matches` : "the 40 most recent runs and ski sessions"}. Click a row to compare it with its suggested set (same workout, same route, most similar), or paste ids above. Also: the Compare button on any activity page.</span></div>
      <Table rows={rows} rowKey={(a) => a.id} onRow={(a) => navigate(`/compare?suggest=${a.id}`)} maxHeight="70vh" empty={acts ? "No activity matches." : "Loading…"} cols={[
        { key: "start", label: "Date", render: (a) => a.date },
        { key: "type", label: "Workout", render: (a) => <span><SportDot sport={a.sport} />{shortWhat(a)}</span> },
        { key: "place", label: "Place", render: (a) => a.place_name ?? a.place ?? a.location ?? "—" },
        { key: "route", label: "Route", render: (a) => <span title={a.route_name ?? ""}>{a.route ?? "—"}</span> },
        { key: "dist_km", label: "km", num: true, render: (a) => km(a.dist_km) },
        { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s) },
        { key: "hr", label: "HR", num: true, render: (a) => num(a.hr) },
      ]} />
    </section>
  );
}

// ---------------------------------------------------------------- session picker
function Picker({ ids, refKind, acts, colors, focus, sug, mode }: {
  ids: string[]; refKind: "first" | "median"; acts: Map<string, Activity>; colors: Record<string, string>; focus: string | null;
  sug: Suggest | null; mode: Mode;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const set = (next: string[], extra: Record<string, string | null> = {}) =>
    setQuery({ ids: next.slice(0, MAX_IDS).join(","), focus: next.includes(focus ?? "") ? focus : null, ...extra }, false);
  const add = () => { const n = parseIds(text); if (n.length) { set([...ids, ...n.filter((i) => !ids.includes(i))]); setText(""); } };
  const extra = useMemo(() => {
    if (!sug) return [];
    const seen = new Set(ids), out: string[] = [];
    for (const s of sug.sets) for (const i of s.ids) if (!seen.has(i)) { seen.add(i); out.push(i); }
    return out.slice(0, 24);
  }, [sug, ids]);
  const label = (id: string) => { const a = acts.get(id) ?? sug?.activities[id]; return a ? `${a.date} ${shortWhat(a)}` : id; };
  return (
    <div class="picker">
      <div class="chips">
        {ids.map((id, k) => (
          <span key={id} class={"schip" + (id === focus ? " on" : "")} title={`${label(id)}${(acts.get(id) ?? sug?.activities[id])?.workout_label ? " — " + (acts.get(id) ?? sug?.activities[id])!.workout_label : ""} (click: highlight)`}
            onClick={() => setQuery({ focus: id === focus ? null : id })}>
            <i style={{ background: colors[id] ?? "#b9bfc6" }} />
            {label(id)}
            {k === 0 && refKind === "first" ? <b class="ref">ref</b> :
              refKind === "first" && <button class="x" title="Make this the reference" onClick={(e) => { e.stopPropagation(); set([id, ...ids.filter((x) => x !== id)]); }}>ref</button>}
            <a class="x" href={`/activities/${id}`} title="Open activity" onClick={(e) => e.stopPropagation()}>↗</a>
            {ids.length > 1 && <button class="x" title="Remove" onClick={(e) => { e.stopPropagation(); set(ids.filter((x) => x !== id)); }}>×</button>}
          </span>
        ))}
        <input class="addid" placeholder="Add ids or paste URLs…" value={text} onInput={(e) => setText((e.target as HTMLInputElement).value)}
          onKeyDown={(e) => e.key === "Enter" && add()} onBlur={add} />
        {sug && sug.sets.length > 0 && <button onClick={() => setOpen(!open)} class={open ? "on" : ""}>Suggestions{open ? " ▴" : " ▾"}</button>}
      </div>
      {sug && sug.comparable === false && <div class="note cmp-warn">No suggestions for {label(sug.id)}: {sug.why_not}</div>}
      {open && sug && (
        <div class="sugg">
          <div class="sets">
            {sug.sets.map((s) => (
              <button key={s.key} title={s.why} onClick={() => { setQuery({ ids: s.ids.join(","), mode: s.mode === mode ? mode : s.mode, focus: null }, false); setOpen(false); }}>
                {s.label} <span class="muted">{s.ids.length}{s.n_total > s.ids.length ? ` of ${s.n_total}` : ""}</span>
              </button>
            ))}
          </div>
          {extra.length > 0 && <div class="chips">
            <span class="muted">Add one:</span>
            {extra.map((id) => <button key={id} class="chip" onClick={() => set([...ids, id])}>+ {label(id)}</button>)}
          </div>}
          <div class="note">Sets for the first session ({sug.date}); each starts with it, then the most recent others on or before its date.</div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- matrix table (sessions × legs / reps / breaks)
interface MCol { key: string; label: string; title?: string }
interface Metric<R> { id: string; label: string; get: (r: R) => N | string | undefined; fmt: (v: any, r: R) => string; best: "min" | "max" | null;
  /** compare every cell with the reference's cell in the same column: tinted green (better) / red (worse), the
   *  difference printed small, plus Mean and Δ columns. Value = the difference that gets the full tint. */
  heat?: number; dfmt?: (d: number) => string }

function Matrix<R>({ units, rows, metrics, metric, setMetric, focus, onHover, onPick, refRow, colors, extraCols = [], cmp, refId, note }: {
  units: MCol[]; rows: { id: string; label: any; cells: (R | null)[] }[]; metrics: Metric<R>[]; metric: string;
  setMetric: (m: string) => void; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void;
  refRow?: { label: string; cells: (R | null)[] } | null; colors: Record<string, string>;
  extraCols?: Col<{ id: string; label: any; cells: (R | null)[] }>[];
  /** the reference's cells (reference session or median) for the heat colouring */
  cmp?: (R | null)[] | null; refId?: string | null; note?: string;
}) {
  const M = metrics.find((m) => m.id === metric) ?? metrics[0];
  type Row = { id: string; label: any; cells: (R | null)[] };
  const all: Row[] = refRow ? [{ id: "_ref", label: <span class="muted">{refRow.label}</span>, cells: refRow.cells }, ...rows] : rows;
  const num = (c: R | null | undefined): N => { if (!c) return null; const v = M.get(c); return typeof v === "number" ? v : null; };
  const best = units.map((_u, k) => bestOf(rows, (r) => { const c = r.cells[k]; return c && !(c as any).partial ? num(c) : null; }, M.best));
  const heat = M.heat && cmp ? M.heat : 0;
  const better = M.best === "max" ? 1 : -1;   // sign of a better difference
  const diff = (r: Row, k: number): N => {
    if (!heat || r.id === "_ref" || r.id === refId) return null;
    const v = num(r.cells[k]), w = num(cmp![k]);
    return v === null || w === null ? null : v - w;
  };
  const tint = (d: N) => {
    if (d === null || Math.abs(d) < 1e-9) return undefined;
    const a = Math.min(1, Math.abs(d) / heat) * 0.32 + 0.04;
    return d * better > 0 ? `rgba(47,138,76,${a.toFixed(2)})` : `rgba(196,60,60,${a.toFixed(2)})`;
  };
  const df = M.dfmt ?? ((d: number) => sgn(d, (x) => String(Math.round(x))));
  const mean = (r: Row) => { const v = r.cells.map(num).filter((x): x is number => x !== null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const meanDiff = (r: Row) => { const d = units.map((_u, k) => diff(r, k)).filter((x): x is number => x !== null); return d.length ? d.reduce((a, b) => a + b, 0) / d.length : null; };
  const bestMean = bestOf(rows, mean, M.best);
  const cols: Col<Row>[] = [
    { key: "label", label: "Session", render: (r) => <span><i class="sw" style={{ background: colors[r.id] ?? "transparent" }} />{r.label}</span>, sort: (r) => (r.id === "_ref" ? "" : String(r.id)) },
    ...units.map((u, k): Col<Row> => ({
      key: u.key, label: u.label, title: u.title, num: true,
      sort: (r) => { const c = r.cells[k]; const v = c ? M.get(c) : null; return typeof v === "number" ? v : v ?? null; },
      render: (r) => {
        const c = r.cells[k];
        if (!c) return <span class="muted">—</span>;
        const v = M.get(c);
        const part = (c as any).partial;
        const d = diff(r, k);
        return <span class={"mcell" + (r.id !== "_ref" && typeof v === "number" && v === best[k] ? " best" : "") + (part ? " partial" : "")}
          style={heat ? { background: tint(d) } : undefined}
          title={part ? `partial: ${Math.round(part * 100)} % of the leg` : d !== null ? `${df(d)} vs the reference` : undefined}>
          {v === null || v === undefined ? "—" : M.fmt(v, c)}{heat ? <small>{d === null ? "" : df(d)}</small> : null}</span>;
      },
    })),
    ...(heat ? [
      { key: "_mean", label: "Mean", num: true, title: "mean over this session's reps", sort: (r: Row) => mean(r),
        render: (r: Row) => { const m = mean(r); return m === null ? "—" : <span class={m === bestMean && r.id !== "_ref" ? "best" : ""}>{M.fmt(m, r.cells.find(Boolean) as R)}</span>; } },
      { key: "_d", label: "Δ ref", num: true, title: "mean difference to the reference over the reps both have (green = better)", sort: (r: Row) => meanDiff(r),
        render: (r: Row) => { const d = meanDiff(r); return d === null ? <span class="muted">{r.id === refId ? "ref" : "—"}</span> : <span class="mcell" style={{ background: tint(d) }}>{df(d)}</span>; } },
    ] as Col<Row>[] : []),
    ...extraCols,
  ];
  return (
    <div>
      <div class="head sm">
        <div class="seg">{metrics.map((m) => <button key={m.id} class={m.id === M.id ? "on" : ""} onClick={() => setMetric(m.id)}>{m.label}</button>)}</div>
        <span class="sub">{heat ? `green = ${M.best === "max" ? "higher" : "lower"} than the ${refId ? "reference" : "median"} in that column, red = ${M.best === "max" ? "lower" : "higher"} (small number = difference); bold green = best per column` : M.best ? `best per column in green (${M.best === "min" ? "lowest" : "highest"})` : ""}{note ? ` · ${note}` : ""}</span>
      </div>
      <Table rows={all} rowKey={(r) => r.id} cols={cols} highlight={focus} onHover={(r) => onHover(r && r.id !== "_ref" ? r.id : null)}
        onRow={(r) => r.id !== "_ref" && onPick(r.id)} maxHeight="520px" />
    </div>
  );
}

// ---------------------------------------------------------------- page
export function ComparePage() {
  const { query } = useLocation();
  const ids = parseIds(query.get("ids") ?? "");
  const suggestId = query.get("suggest");
  const mode = (MODES.some((m) => m.id === query.get("mode")) ? query.get("mode") : "distance") as Mode;
  const refKind = query.get("ref") === "median" ? "median" : "first";
  const repAxis = query.get("rep_axis") === "normalized" ? "normalized" : "absolute";
  const gap = query.get("gap") ?? "60", rgap = query.get("rgap") ?? "30";
  const match = query.get("match") === "index" ? "index" : "auto";
  const deltaKind = query.get("delta") === "elapsed" ? "elapsed" : "moving";
  const pinned = query.get("focus");
  const tab = query.get("tab");
  const [hover, setHover] = useState<string | null>(null);
  const [cur, setCur] = useState<number | null>(null);
  const focus = hover ?? pinned;

  const acts = useApi<{ activities: Activity[] }>("/api/activities").data;
  const actMap = useMemo(() => new Map((acts?.activities ?? []).map((a) => [a.id, a])), [acts]);
  const sug = useApi<Suggest>(suggestId ? `/api/compare/suggest?id=${suggestId}` : ids[0] ? `/api/compare/suggest?id=${ids[0]}` : null);

  useEffect(() => {
    if (!suggestId) return;
    if (sug.data) {
      if (sug.data.comparable === false) return;   // the page says why
      const s = pickSet(sug.data);
      navigate(`/compare${qs({ ids: s ? s.ids.join(",") : suggestId, mode: s?.mode ?? sug.data.mode })}`, true);
    } else if (sug.error) navigate(`/compare${qs({ ids: suggestId, mode: "moving" })}`, true);
  }, [suggestId, sug.data, sug.error]);

  const iv = mode === "intervals" || mode === "breaks";
  // modes the engine can run for the first session (from the suggest call); distance modes 422 without distance
  const allowed = sug.data && sug.data.id === ids[0] ? sug.data.modes ?? null : null;
  const url = ids.length && !suggestId ? `/api/compare${qs({
    ids: ids.join(","), mode, reference: refKind,
    rep_axis: mode === "intervals" && repAxis !== "absolute" ? repAxis : null,
    gap_s: mode === "intervals" && gap !== "60" ? gap : null, rep_gap_s: mode === "breaks" && rgap !== "30" ? rgap : null,
    match: iv && match !== "auto" ? match : null,
  })}` : null;
  const { data: r, error, loading } = useApi<CmpResult>(url);
  useEffect(() => { document.title = "Compare"; }, []);
  useEffect(() => setCur(null), [r]);

  const firstSport = (r?.sessions[0]?.sport ?? actMap.get(ids[0])?.sport) || "run";
  // breaks view: speed only (pace is meaningless while standing / walking)
  const tableMetric = (query.get("metric") as "pace" | "speed") || (firstSport === "run" ? "pace" : "speed");
  const metric = mode === "breaks" ? "speed" : tableMetric;

  // ---- derived chart data
  const D = useMemo<DD | null>(() => {
    if (!r || !r.series || !Object.keys(r.series).length) return null;
    const sids = r.ids.map(String).filter((i) => r.series[i]);
    const refId = r.reference.kind === "session" ? String(r.reference.id) : null;
    const { colors, many } = colorsFor(sids, refId);
    const order = refId ? [...sids.filter((i) => i !== refId), refId] : sids;
    const X = buildGrid(r);
    // aligned modes mix squeezed (dense) and real-time (sparse) stretches: a fixed max gap instead of 4 x median step
    const g = (id: string, k: string) => interp(r.series[id].x, r.series[id][k], X, iv ? 8 : undefined);
    // display smoothing (grid points): a little in the aligned views, ~1/200 of the range in the time views
    const sk = iv ? 2 : mode === "moving" || mode === "elapsed" ? Math.round(X.length / 200) : 0;
    const speed: Record<string, N[]> = {}, hr: Record<string, N[]> = {}, alt: Record<string, N[]> = {}, delta: Record<string, N[]> = {},
      t: Record<string, N[]> = {}, mov: Record<string, N[]> = {}, raw: Record<string, N[]> = {}, repSpeed: Record<string, N[]> = {};
    for (const id of sids) {
      raw[id] = g(id, "speed_kmh");
      if (mode === "intervals" && r.series[id].phase) {
        // rep points only (phase 1): the squeezed breaks must not count as distance for the in-rep gap
        const ph = g(id, "phase");
        repSpeed[id] = raw[id].map((v, i) => (ph[i] !== null && Math.abs(ph[i]! - 1) < 1e-6 ? v : null));
      } else repSpeed[id] = raw[id];
      speed[id] = sk ? smoothN(raw[id], sk) : raw[id];
      hr[id] = sk && !iv ? smoothN(g(id, "hr"), sk) : g(id, "hr");
      if (mode === "distance") {
        delta[id] = g(id, deltaKind === "moving" ? "delta_moving_s" : "delta_elapsed_s");
        t[id] = g(id, "elapsed_s"); mov[id] = g(id, deltaKind === "moving" ? "moving_s" : "elapsed_s");
        if (!r.route_ref) alt[id] = g(id, "alt");
      } else if (mode === "moving" || mode === "elapsed") {
        delta[id] = g(id, "ahead_m"); t[id] = g(id, "t"); alt[id] = g(id, "alt");
      } else t[id] = g(id, "t");
    }
    const routeAlt = r.route_ref ? interp(r.route_ref.m, r.route_ref.alt, X, 1e9) : null;
    return { sids, refId, colors, many, order, X, speed, raw, repSpeed, hr, alt, delta, t, mov, routeAlt };
  }, [r, deltaKind, iv]);

  const panels = useMemo<CPanel[]>(() => {
    if (!D || !r) return [];
    const withBand = D.many || refKind === "median";
    const mk = (key: string, label: string, ys: Record<string, N[]>, fmt: (v: number) => string, height: number, extra: Partial<CPanel> = {}): CPanel => {
      const st = withBand ? stats(D.sids.map((i) => ys[i]).filter(Boolean)) : null;
      return { key, label, ys, fmt, height, band: st && D.sids.length >= 3 ? { lo: st.lo, hi: st.hi } : null,
        median: st && refKind === "median" && !extra.zero ? st.med : null, ...extra };
    };
    const pace = metric === "pace";
    const sp: Record<string, N[]> = {};
    for (const i of D.sids) sp[i] = pace ? D.speed[i].map(kmhToPace) : D.speed[i];
    const P: CPanel[] = [];
    const speedP = mk("speed", pace ? "Pace (min/km)" : "Speed (km/h)", sp, pace ? (v) => dur(v) : (v) => v.toFixed(1), 170, pace ? { dir: -1 } : {});
    const hrP = mk("hr", "Heart rate (bpm)", D.hr, (v) => String(Math.round(v)), 130);
    if (mode === "distance") {
      P.push(mk("delta", `Gap to ${refKind === "median" ? "median" : "reference"} (${deltaKind} time, + behind; up = ahead)`, D.delta,
        (v) => sgn(v, dur), 160, { zero: true, dir: -1 }));
      P.push(speedP, hrP);
      if (D.routeAlt) P.push({ key: "alt", label: "Elevation along the route (m)", ys: {}, single: D.routeAlt, fmt: (v) => String(Math.round(v)), height: 90 });
      else P.push(mk("alt", "Elevation (m)", D.alt, (v) => String(Math.round(v)), 90));
    } else if (mode === "moving" || mode === "elapsed") {
      P.push(speedP, hrP);
      P.push(mk("delta", `Distance ahead of ${refKind === "median" ? "median" : "reference"} (m, at the same ${mode} time)`, D.delta,
        (v) => sgn(v, (x) => (x >= 1000 ? `${(x / 1000).toFixed(x >= 10000 ? 0 : 1)} km` : `${Math.round(x)} m`)), 140, { zero: true }));
      P.push(mk("alt", "Elevation (m)", D.alt, (v) => String(Math.round(v)), 90));
    } else if (mode === "intervals") {
      // y range of pace / speed from the reps only (breaks would squash them)
      const sp = r ? spansOf(r) : [];
      speedP.rangeMask = D.X.map((x) => sp.some((q) => x >= q.a + 10 && x <= q.b));
      const has = (ys: Record<string, N[]>) => Object.values(ys).some((a) => a.some((v) => v !== null));
      if (has(speedP.ys)) P.push(speedP);   // sessions without distance: HR only
      // gap inside each rep (seconds at the same distance): the clearest "who is faster in this rep, by how much"
      if (r.axis?.rep_axis !== "normalized" && r.axis?.slot_x0) {
        const refSp = D.refId ? D.repSpeed[D.refId] : stats(D.sids.map((i) => D.repSpeed[i])).med;
        const gaps: Record<string, N[]> = {};
        for (const i of D.sids) gaps[i] = repGap(D.X, r.axis.slot_x0, r.axis.slot_width, D.repSpeed[i], refSp);
        if (has(gaps)) P.push(mk("gap", `Gap within each rep (s at the same distance; + behind the ${refKind === "median" ? "median" : "reference"}, up = ahead)`, gaps,
          (v) => sgn(v, (x) => (x >= 60 ? dur(x) : `${x.toFixed(x < 10 ? 1 : 0)} s`)), 130, { zero: true, dir: -1, axis: "num" }));
      }
      P.push(hrP);
    }
    else { P.push(hrP); if (Object.values(speedP.ys).some((a) => a.some((v) => v !== null))) P.push(speedP); }
    return P;
  }, [D, metric, refKind, mode]);

  const ticks = useMemo(() => (r ? alignedTicks(r) : null), [r]);
  const sp = useMemo(() => (r ? spansOf(r) : []), [r]);
  const xFmt = (x: number) => (mode === "distance" ? `${(x / 1000).toFixed(x % 1000 ? 1 : 0)} km` : dur(x));

  // ---- positions at the cursor. distance mode: ghost race = where each session was when the reference
  // (or the median) reached x, in moving or elapsed time
  const positions = useMemo(() => {
    const out: Record<string, [number, number] | null> = {};
    if (!D || !r?.tracks || cur === null) return out;
    if (mode === "distance") {
      const T0 = D.refId ? D.mov[D.refId][cur] : r.median?.moving_s ? interp(r.median.x as number[], r.median.moving_s, [D.X[cur]], 1e9)[0] : null;
      for (const id of D.sids) {
        const m = D.mov[id], te = D.t[id];
        if (T0 === null || T0 === undefined) { out[id] = posAt(r.tracks[id], te[cur]); continue; }
        // first bin where this session's time >= T0 -> its elapsed time there
        let k = -1;
        for (let i = 0; i < m.length; i++) if (m[i] !== null && m[i]! >= T0) { k = i; break; }
        out[id] = k >= 0 ? posAt(r.tracks[id], te[k]) : null;
      }
      return out;
    }
    for (const id of D.sids) out[id] = posAt(r.tracks[id], D.t[id][cur]);
    return out;
  }, [cur, D, r, mode]);

  const pick = (id: string) => setQuery({ focus: id === pinned ? null : id });

  // ---- render
  if (suggestId) return <main><div class="empty">{sug.error ? sug.error
    : sug.data?.comparable === false ? <>This activity can't be compared: {sug.data.why_not ?? "no usable data"}. <a href={`/activities/${suggestId}`}>Back to the activity</a></>
    : "Finding sessions to compare…"}</div></main>;
  const head = (
    <section class="first">
      <div class="head">
        <h1>Compare</h1>
        <span class="sub">{ids.length ? `${ids.length} session${ids.length > 1 ? "s" : ""}` : "one session vs one or many"}
          {r ? ` · ${r.compute_s} s` : ""}{loading ? " · loading…" : ""}</span>
        {ids.length > 0 && <div class="tools">
          <span class="muted">Reference</span>
          <div class="seg">
            <button class={refKind === "first" ? "on" : ""} onClick={() => setQuery({ ref: null })} title="The first session is the reference">First session</button>
            <button class={refKind === "median" ? "on" : ""} onClick={() => setQuery({ ref: "median" })} title="Median of all selected sessions">Median</button>
          </div>
        </div>}
      </div>
      <Picker ids={ids} refKind={refKind} acts={actMap} colors={D?.colors ?? {}} focus={pinned} sug={sug.data} mode={mode} />
      {ids.length > 0 && (
        <div class="modebar">
          <div class="seg tabs">
            {MODES.map((m) => {
              const off = !!allowed && !allowed.includes(m.id) && m.id !== mode;
              return <button key={m.id} class={m.id === mode ? "on" : ""} disabled={off}
                title={off ? `${m.label}: not available — the first session has no distance (GPS / foot pod); use the time or interval views` : m.title}
                onClick={() => setQuery({ mode: m.id, tab: null })}>{m.label}</button>;
            })}
          </div>
          <div class="opts">
            {(mode !== "breaks") && <div class="seg">
              <button class={metric === "pace" ? "on" : ""} onClick={() => setQuery({ metric: firstSport === "run" ? null : "pace" })}>Pace</button>
              <button class={metric === "speed" ? "on" : ""} onClick={() => setQuery({ metric: firstSport === "run" ? "speed" : null })}>Speed</button>
            </div>}
            {mode === "distance" && <><span class="muted">Gap in</span><div class="seg">
              <button class={deltaKind === "moving" ? "on" : ""} onClick={() => setQuery({ delta: null })} title="Stops don't count">Moving time</button>
              <button class={deltaKind === "elapsed" ? "on" : ""} onClick={() => setQuery({ delta: "elapsed" })} title="Stops count">Elapsed</button>
            </div></>}
            {mode === "intervals" && <>
              <span class="muted">Rep axis</span>
              <div class="seg">
                <button class={repAxis === "absolute" ? "on" : ""} onClick={() => setQuery({ rep_axis: null })} title="Seconds since the rep start">Seconds</button>
                <button class={repAxis === "normalized" ? "on" : ""} onClick={() => setQuery({ rep_axis: "normalized" })} title="0-100 % of each rep">% of rep</button>
              </div>
              <span class="muted">Break width</span>
              <select value={gap} onChange={(e) => setQuery({ gap: (e.target as HTMLSelectElement).value === "60" ? null : (e.target as HTMLSelectElement).value })}>
                {["15", "30", "60", "90", "120"].map((v) => <option key={v} value={v}>{v} s</option>)}
              </select>
            </>}
            {mode === "breaks" && <>
              <span class="muted">Rep width</span>
              <select value={rgap} onChange={(e) => setQuery({ rgap: (e.target as HTMLSelectElement).value === "30" ? null : (e.target as HTMLSelectElement).value })}>
                {["10", "20", "30", "60"].map((v) => <option key={v} value={v}>{v} s</option>)}
              </select>
            </>}
            {iv && <><span class="muted">Match reps</span><div class="seg">
              <button class={match === "auto" ? "on" : ""} onClick={() => setQuery({ match: null })} title="Pair reps by distance / duration (order kept)">By length</button>
              <button class={match === "index" ? "on" : ""} onClick={() => setQuery({ match: "index" })} title="Rep 1 vs rep 1, …">By number</button>
            </div></>}
          </div>
        </div>
      )}
      {r && <ModeNote r={r} />}
    </section>
  );

  if (!ids.length) return <main class="cmp">{head}<Landing /></main>;
  if (error) return <main class="cmp">{head}<div class="err">{error}{iv ? " — reps exist only for runs classified as intervals and ski interval sessions; try Moving time or Route position."
    : allowed && !allowed.includes(mode) ? " — the first session has no distance; try Moving time, Elapsed or Intervals." : ""}</div></main>;
  if (!r || !D) return <main class="cmp">{head}<div class="empty">{loading ? "Comparing…" : "Nothing to show."}</div></main>;

  return (
    <main class="cmp">
      {head}
      <section>
        <div class="cmp-top">
          <div class="cmp-left">
            <div class="cmp-map">
              <CompareMap tracks={r.tracks ?? {}} order={D.order} colors={D.colors} refId={D.refId} focus={focus} many={D.many}
                positions={positions} routeRef={r.route_ref} onPick={setHover} onClick={pick} />
            </div>
            <CursorLegend r={r} D={D} panels={panels} cur={cur} focus={focus} onHover={setHover} onPick={pick} pace={tableMetric === "pace"} />
          </div>
          <div class="cmp-right">
            <CompareCharts X={D.X} panels={panels} order={D.order} colors={D.colors} refId={D.refId} focus={focus} many={D.many}
              spans={sp} ticks={ticks} xFmt={xFmt} xIncrs={mode === "distance" ? [100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000] : [30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200]} onCursor={setCur} onPick={setHover}
              names={Object.fromEntries(D.sids.map((i) => [i, r.activities[i]?.date ?? i]))} />
            <div class="note">Drag on a chart to zoom, double-click to reset. Hover a line, a track, a chip or a table row to highlight a session; click to keep it highlighted.
              {D.many ? " Grey band = middle 50 % of the sessions." : ""}</div>
          </div>
        </div>
      </section>
      {iv && <Detail r={r} D={D} focus={focus} onHover={setHover} onPick={pick} tab={tab} metric={tableMetric} />}
      <section>
        <div class="head"><h2>Sessions</h2><span class="sub">{iv ? "reps summary first, then the whole session; " : ""}deltas vs the {refKind === "median" ? "median of the selection" : "reference (first session)"}; best per column in green</span></div>
        <SummaryTable r={r} D={D} focus={focus} onHover={setHover} onPick={pick} deltaKind={deltaKind} metric={tableMetric} />
      </section>
      {!iv && <Detail r={r} D={D} focus={focus} onHover={setHover} onPick={pick} tab={tab} metric={tableMetric} />}
    </main>
  );
}

function ModeNote({ r }: { r: CmpResult }) {
  const a = r.axis || {};
  const bits: string[] = [];
  if (r.mode === "distance") bits.push(a.route ? `x = position along route ${a.route} (${a.route_name}); markers on the map: where everyone was when the ${r.reference.kind === "median" ? "median" : "reference"} reached the cursor` : "x = own distance (sessions don't share one compared route)");
  if (r.mode === "moving") bits.push("x = moving time; markers = everyone at the same moving time");
  if (r.mode === "elapsed") bits.push("x = elapsed time; markers = everyone at the same clock time");
  if (r.mode === "intervals") bits.push(`reps start at the same x; breaks squeezed to ${a.gap_s} s; ${a.rep_axis === "normalized" ? "x within a rep = % of the rep" : "x within a rep = seconds since its start"}`);
  if (r.mode === "breaks") bits.push(`breaks start at the same x (real seconds); reps squeezed to ${a.rep_gap_s} s; after the last rep up to 2 min of recovery`);
  if ((r.mode === "intervals" || r.mode === "breaks") && r.sessions.some((s) => s.sport === "ski"))
    bits.push("ski: reps = the hard efforts between stops (laps or climbs found by ski_intervals), so reps of different sessions can be on different parts of a climb");
  if (r.skipped.length) bits.push(`skipped: ${r.skipped.map((s) => `${s.id} (${s.why})`).join(", ")}`);
  if (r.no_reps?.length) bits.push(`no reps in ${r.no_reps.join(", ")}`);
  const sports = [...new Set(r.sessions.map((s) => s.sport))];
  return <>
    <div class="note">{bits.join(" · ")}</div>
    {sports.length > 1 && <div class="note cmp-warn">⚠ Mixed sports ({sports.map((x) => SPORT_LABEL[x] ?? x).join(", ")}): paces, speeds and efficiency are not comparable across sports.</div>}
  </>;
}

/** chart data derived from one /api/compare result: every session on the shared x grid */
interface DD {
  sids: string[]; refId: string | null; colors: Record<string, string>; many: boolean; order: string[]; X: number[];
  speed: Record<string, N[]>; raw: Record<string, N[]>; repSpeed: Record<string, N[]>; hr: Record<string, N[]>; alt: Record<string, N[]>; delta: Record<string, N[]>; t: Record<string, N[]>;
  mov: Record<string, N[]>; routeAlt: N[] | null;
}

// ---------------------------------------------------------------- cursor legend (hover values per session)
function CursorLegend({ r, D, panels, cur, focus, onHover, onPick, pace }: {
  r: CmpResult; D: DD; panels: CPanel[]; cur: number | null; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void;
  pace?: boolean;
}) {
  if (cur === null && r.mode === "intervals" && r.reps && pace !== undefined) {
    const ids = D.sids.filter((i) => r.reps![i]);
    return <RepRank r={r} D={D} RD={repData(r, D, ids, pace)} focus={focus} onHover={onHover} onPick={onPick} compact />;
  }
  if (cur === null) return <div class="legend empty">Hover the charts to see every session at that point{r.mode === "distance" ? " (and on the map: where each was at the reference's time)" : ""}.</div>;
  const x = D.X[cur];
  const P = panels.filter((p) => !p.single);
  const dP = P.find((p) => p.key === "delta" || p.key === "gap");
  const rows = D.sids.map((id) => ({ id, v: P.map((p) => p.ys[id]?.[cur] ?? null) }));
  const di = dP ? P.indexOf(dP) : -1;
  rows.sort((a, b) => {
    const va = di >= 0 ? a.v[di] : a.v[0], vb = di >= 0 ? b.v[di] : b.v[0];
    if (va === null) return 1; if (vb === null) return -1;
    // distance-mode gap: + = behind -> ascending; ahead_m: descending; pace ascending; speed / HR descending
    return r.mode === "distance" || dP?.key === "gap" || (di < 0 && P[0]?.dir === -1) ? va - vb : vb - va;
  });
  const alt = panels.find((p) => p.single)?.single?.[cur];
  return (
    <div class="legend">
      <div class="lh"><b>{xLabel(r, x)}</b>{alt !== null && alt !== undefined ? <span class="muted"> · {Math.round(alt)} m</span> : null}</div>
      <table>
        <thead><tr><th>Session</th>{P.map((p) => <th key={p.key} class="num">{p.key === "delta" ? (r.mode === "distance" ? "Gap" : "Ahead") : p.key === "hr" ? "HR" : p.label.split(" ")[0]}</th>)}</tr></thead>
        <tbody onMouseLeave={() => onHover(null)}>
          {rows.map(({ id, v }) => {
            const a = r.activities[id];
            return (
              <tr key={id} class={id === focus ? "hl" : ""} onMouseEnter={() => onHover(id)} onClick={() => onPick(id)}>
                <td><i class="sw" style={{ background: id === focus && D.many && id !== D.refId ? FOCUS_MANY : D.colors[id] }} />{a?.date ?? id}{id === D.refId ? <span class="muted"> ref</span> : ""}</td>
                {v.map((x, k) => <td key={k} class="num">{x === null ? "—" : P[k].fmt(x)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- summary table
/** pace (s/km) <-> km/h for the tables: every pace-like column follows the Pace / Speed toggle */
const toKmh = (p: N | undefined) => (p ? 3600 / p : null);

function SummaryTable({ r, D, focus, onHover, onPick, deltaKind, metric }: {
  r: CmpResult; D: DD; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void; deltaKind: string; metric: string;
}) {
  type Row = CmpSession & { sid: string; a: Activity | null; fin: N; rs: any };
  const rows: Row[] = r.sessions.map((s) => {
    const sid = String(s.id);
    let fin: N = null;
    if (r.mode === "distance") {
      const d = r.series[sid]?.[deltaKind === "moving" ? "delta_moving_s" : "delta_elapsed_s"] as N[] | undefined;
      if (d) for (let i = d.length - 1; i >= 0; i--) if (d[i] !== null) { fin = d[i]; break; }
    }
    return { ...s, sid, a: r.activities[sid], fin, rs: r.rep_summary?.[sid] ?? null };
  });
  const run = r.sessions[0]?.sport === "run";
  const pace = metric === "pace";
  const iv = !!r.rep_summary;
  const refRow = rows.find((x) => x.sid === D.refId) ?? null;
  const cols: Col<Row>[] = [];
  const add = (key: string, label: string, get: (r: Row) => N | undefined, fmt: (v: number, r: Row) => string, best: "min" | "max" | null, title?: string, tip?: (r: Row) => string) => {
    const b = bestOf(rows, get, best);
    cols.push({ key, label, num: true, title, sort: (x) => get(x) ?? null, render: (x) => {
      const v = get(x);
      return v === null || v === undefined ? "—" : <span title={tip?.(x)}><B v={v} best={b}>{fmt(v, x)}</B></span>;
    } });
  };
  const hrTip = (x: Row) => `moving ${x.hr_avg ?? "—"} · whole activity ${x.hr_avg_activity ?? "—"} (as on the activity page)`;
  cols.push({ key: "date", label: "Session", sort: (x) => x.date, render: (x) => (
    <span><i class="sw" style={{ background: x.sid === focus && D.many && x.sid !== D.refId ? FOCUS_MANY : D.colors[x.sid] }} />
      <a href={`/activities/${x.sid}`} onClick={(e) => e.stopPropagation()}>{x.date}</a>{x.sid === D.refId ? <span class="muted"> ref</span> : ""}</span>) });
  cols.push({ key: "what", label: "Workout", sort: (x) => x.sig ?? x.type ?? "", render: (x) => <span class="muted" title={x.a?.workout_label ?? ""}>{x.sig || shortWhat(x.a) || stypeLabel(x.type)}</span> });
  if (iv) {
    // interval modes: the rep summary first, the whole session after it
    const pk = (x: Row) => x.rs?.rep_pace_s_km as N | undefined;
    add("n_reps", "Reps", (x) => x.rs?.n_reps, (v) => String(v), null);
    if (pace) add("rep_pace", "Rep pace", pk, (v) => dur(v), "min", "mean pace over all reps (min/km)");
    else add("rep_kmh", "Rep km/h", (x) => toKmh(pk(x)), (v) => v.toFixed(1), "max", "mean speed over all reps");
    add("d_rep", pace ? "Δ s/km" : "Δ km/h", (x) => {
      const d = x.rs?.d_rep_pace_matched_s_km as N | undefined;
      if (d === null || d === undefined || pace) return d;
      // speed: same matched comparison, converted around this session's own rep pace
      const p = pk(x);
      return p ? 3600 / p - 3600 / (p - d) : null;
    }, (v) => (pace ? sgn(v, (s) => `${Math.round(s)}`) : sgn(v, (s) => s.toFixed(1))), pace ? "min" : "max",
      `rep ${pace ? "pace (s/km)" : "speed (km/h)"} vs the reference over the reps both have (matched slots)`);
    add("rep_hr", "Rep HR", (x) => x.rs?.rep_hr, (v) => String(v), null, "mean HR during the reps");
    add("brk", "Rest", (x) => x.rs?.break_median_s, (v) => dur(v), null, "this session's median break between reps (measured, not the “rest ~2:30” group bin)");
    add("drop", "HR drop 60 s", (x) => x.rs?.drop_60_median, (v) => String(v), "max", "median HR drop in the first 60 s of the breaks");
    cols.push({ key: "match", label: "Matched", title: "rep slots matched with the reference · how reps were paired",
      render: (x) => <span class="muted">{x.rs ? `${x.rs.matched_slots ?? "—"} · ${x.rs.match}` : "no reps"}</span>, sort: (x) => x.rs?.match ?? "" });
    add("dist_m", "km", (x) => x.dist_m, (v) => (v / 1000).toFixed(2), null);
    add("moving_s", "Moving", (x) => x.moving_s, (v) => dur(v), null, "moving time of the whole session");
    add("hr", "HR moving", (x) => x.hr_avg, (v) => String(v), null, "average HR while moving (whole session; the activity page shows the whole-activity average)", hrTip);
    return <Table rows={rows} rowKey={(x) => x.sid} cols={cols} highlight={focus} onHover={(x) => onHover(x ? x.sid : null)} onRow={(x) => onPick(x.sid)} maxHeight="560px" />;
  }
  // deltas vs the reference: own columns for time and pace, tooltips for the rest
  const dTip = (get: (x: Row) => N | undefined, f: (v: N) => string, what: string) => (x: Row) =>
    x.sid === D.refId ? "reference" : `${f(get(x) ?? null)} ${what} vs the ${refRow ? "reference" : "median"}`;
  add("dist_m", "km", (x) => x.dist_m, (v) => (v / 1000).toFixed(2), null);
  if (r.mode === "distance") add("fin", "Gap at end", (x) => x.fin, (v) => sgn(v, dur), "min", `${deltaKind} time behind (+) / ahead (−) of the reference at the last common point`);
  add("moving_s", "Moving", (x) => x.moving_s, (v) => dur(v), null);
  add("d_moving", "Δ", (x) => x.delta.moving_s, (v) => sgn(v, dur), null, "moving time vs reference");
  add("elapsed_s", "Elapsed", (x) => x.elapsed_s, (v) => dur(v), null);
  add("pause_s", "Stopped", (x) => x.pause_s, (v, x) => `${dur(v)}${x.n_pauses ? ` (${x.n_pauses})` : ""}`, null, "paused time (number of pauses)");
  if (pace) add("pace", "Pace", (x) => x.moving_pace_s_km, (v) => dur(v), "min", "moving pace (min/km)");
  else add("kmh", "km/h", (x) => x.moving_kmh, (v) => v.toFixed(1), "max", "moving speed");
  add("d_pace", "Δ", (x) => (pace ? x.delta.moving_pace_s_km : x.delta.moving_kmh), (v) => (pace ? sgn(v, (s) => `${Math.round(s)} s`) : sgn(v, (s) => s.toFixed(1))), null, pace ? "s/km vs reference" : "km/h vs reference");
  if (run) add("gap", pace ? "GAP" : "GAP km/h", (x) => (pace ? x.gap_pace_s_km : toKmh(x.gap_pace_s_km)), (v) => (pace ? dur(v) : v.toFixed(1)), pace ? "min" : "max", "grade-adjusted pace (Minetti)");
  add("hr", "HR moving", (x) => x.hr_avg, (v) => String(v), null, "average HR while moving (stops excluded); hover for Δ and the whole-activity average shown on the activity page",
    (x) => `${hrTip(x)} · ${dTip((y) => y.delta.hr_avg, (v) => sgn(v), "bpm")(x)}`);
  add("hr_max", "Max", (x) => x.hr_max, (v) => String(v), null);
  add("climb", "Climb", (x) => x.climb_m, (v) => String(v), null);
  add("eff", "m/beat", (x) => x.eff_m_per_beat, (v) => v.toFixed(2), "max", "distance per heartbeat (GAP speed for runs): higher = more economical; hover for Δ %",
    dTip((y) => y.delta.eff_pct, (v) => sgn(v, (s) => s.toFixed(1)) + " %", "efficiency"));
  return <Table rows={rows} rowKey={(x) => x.sid} cols={cols} highlight={focus} onHover={(x) => onHover(x ? x.sid : null)} onRow={(x) => onPick(x.sid)} maxHeight="560px" />;
}

// ---------------------------------------------------------------- legs / reps / breaks
function Detail({ r, D, focus, onHover, onPick, tab, metric }: {
  r: CmpResult; D: DD; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void; tab: string | null; metric: string;
}) {
  const mq = (k: string) => (v: string) => setQuery({ [k]: v });
  const { query } = useLocation();
  const label = (id: string) => { const a = r.activities[id]; return <span>{a?.date ?? id}{id === D.refId ? <span class="muted"> ref</span> : ""}</span>; };
  const pace = metric === "pace";
  if (r.mode === "distance" && r.legs?.length) {
    type L = NonNullable<CmpResult["legs"]>[number]["sessions"][string] & {};
    const units = r.legs.map((l, k) => ({ key: "l" + k, label: `${(l.from_m / 1000).toFixed(1)}–${(l.to_m / 1000).toFixed(1)}`,
      title: `${l.name}${l.climb_m !== null ? `, climb ${l.climb_m} m` : ""}` }));
    const rows = D.sids.map((id) => ({ id, label: label(id), cells: r.legs!.map((l) => l.sessions[id] as L | null) }));
    const metrics: Metric<L>[] = [
      { id: "time", label: "Moving time", get: (c) => c.moving_s, fmt: (v) => dur(v), best: "min" },
      { id: "d", label: "Δ leg", get: (c) => c.d_moving_s, fmt: (v) => sgn(v, dur), best: "min" },
      { id: "gap", label: "Gap at leg end", get: (c) => c.delta_moving_s, fmt: (v) => sgn(v, dur), best: "min" },
      pace ? { id: "speed", label: "Pace", get: (c) => (c.speed_kmh ? 3600 / c.speed_kmh : null), fmt: (v) => dur(v), best: "min" }
        : { id: "speed", label: "km/h", get: (c) => c.speed_kmh, fmt: (v) => v.toFixed(1), best: "max" },
      { id: "hr", label: "HR", get: (c) => c.hr, fmt: (v) => String(v), best: null },
      { id: "stop", label: "Stopped", get: (c) => c.pause_s, fmt: (v) => dur(v), best: null },
      { id: "rank", label: "Rank", get: (c) => c.rank, fmt: (v) => String(v), best: "min" },
    ];
    let refRow = null;
    if (r.reference.kind === "median") {
      refRow = { label: "Median", cells: r.legs.map((l) => {
        const v = Object.values(l.sessions).filter(Boolean).map((x) => x!.moving_s).sort((a, b) => a - b);
        return v.length ? ({ moving_s: v[v.length >> 1] } as L) : null;
      }) };
    }
    return (
      <section>
        <div class="head"><h2>Legs</h2><span class="sub">{r.legs_kind === "route_waypoints" ? "between the route's common stops" : r.legs_kind === "laps_400m" ? "400 m laps (own distance)" : "per km (own distance)"}; Δ leg = leg time − reference leg time; gap = ghost gap at the leg end (moving, + behind); faded = partly covered</span></div>
        <Matrix units={units} rows={rows} metrics={metrics} metric={query.get("lm") ?? "time"} setMetric={mq("lm")} focus={focus}
          onHover={onHover} onPick={onPick} refRow={refRow} colors={D.colors} />
      </section>
    );
  }
  if ((r.mode === "intervals" || r.mode === "breaks") && r.slots && r.reps) {
    const slots = r.slots;
    const repUnits = slots.map((s) => ({ key: "s" + s.slot, label: slotName(s, true), title: s.anchor ? `${slotName(s)}: the reference's rep ${s.label} and the reps matched to it (${s.n_sessions} sessions)`
      : `${slotName(s)}: a rep that only some sessions have (${s.n_sessions}), between the reference's rep ${s.label.split("+")[0]} and the next` }));
    const ids = D.sids.filter((i) => r.reps![i]);
    type R = NonNullable<NonNullable<CmpResult["reps"]>[string][number]>;
    type Bk = NonNullable<NonNullable<CmpResult["breaks"]>[string][number]>;
    const repMetrics: Metric<R>[] = [
      pace ? { id: "pace", label: "Pace", get: (c) => c.pace_s_km, fmt: (v) => dur(v), best: "min", heat: 8, dfmt: (d) => sgn(d) }
        : { id: "pace", label: "km/h", get: (c) => c.speed_kmh, fmt: (v) => v.toFixed(1), best: "max", heat: 0.6, dfmt: (d) => sgn(d, (x) => x.toFixed(1)) },
      { id: "time", label: "Time", get: (c) => c.time_s, fmt: (v) => dur(v), best: "min", heat: 4, dfmt: (d) => sgn(d, (x) => x.toFixed(Math.abs(x) < 10 ? 1 : 0)) },
      { id: "d", label: "Δ time", get: (c) => c.d_time_s, fmt: (v) => sgn(v, (s) => `${s.toFixed(1)} s`), best: "min" },
      { id: "dist", label: "Distance", get: (c) => c.dist_m, fmt: (v) => `${v}`, best: null },
      { id: "hr", label: "HR", get: (c) => c.hr, fmt: (v) => String(v), best: null },
      { id: "hrmax", label: "HR max", get: (c) => c.hr_max, fmt: (v) => String(v), best: null },
      { id: "hrend", label: "HR at end", get: (c) => c.hr_end, fmt: (v) => String(v), best: null },
    ];
    const brkMetrics: Metric<Bk>[] = [
      { id: "dur", label: "Duration", get: (c) => c.dur_s, fmt: (v) => dur(v), best: null },
      { id: "d", label: "Δ duration", get: (c) => c.d_dur_s, fmt: (v) => sgn(v, (s) => `${Math.round(s)} s`), best: null },
      { id: "kind", label: "Kind", get: (c) => c.kind, fmt: (v, c) => `${v} ${Math.round(c.standing_s)}s`, best: null },
      { id: "drop30", label: "HR drop 30 s", get: (c) => c.drop_30, fmt: (v) => String(v), best: "max", heat: 8 },
      { id: "drop60", label: "HR drop 60 s", get: (c) => c.drop_60, fmt: (v) => String(v), best: "max", heat: 10 },
      { id: "peak", label: "HR peak", get: (c) => c.hr_peak, fmt: (v) => String(v), best: null },
      { id: "min", label: "HR min", get: (c) => c.hr_min, fmt: (v) => String(v), best: null },
      { id: "next", label: "HR at next start", get: (c) => c.hr_next_start, fmt: (v) => String(v), best: "min", heat: 10 },
      { id: "dist", label: "Distance", get: (c) => c.dist_m, fmt: (v) => `${v} m`, best: null },
    ];
    // a break sits in the slot of the rep before it; the last slot has no break
    const brkUnits = slots.slice(0, -1).map((s) => ({ key: "b" + s.slot, label: `After ${slotName(s, true)}`, title: `break after ${slotName(s)}` }));
    const repRef = r.reps_ref ? { label: "Median", cells: r.reps_ref as any[] } : null;
    const brkRef = r.breaks_ref ? { label: "Median", cells: (r.breaks_ref as any[]).slice(0, -1) } : null;
    const repCmp = (D.refId ? r.reps[D.refId] : (r.reps_ref as any[] | null)) ?? null;
    const brkCmp = (D.refId ? r.breaks?.[D.refId]?.slice(0, -1) : (r.breaks_ref as any[] | null)?.slice(0, -1)) ?? null;
    const reps = (
      <Matrix<R> units={repUnits} rows={ids.map((id) => ({ id, label: label(id), cells: r.reps![id] }))} metrics={repMetrics}
        metric={query.get("rm") ?? (ids.some((i) => r.reps![i].some((c) => c?.pace_s_km)) ? "pace" : "time")} setMetric={mq("rm")} focus={focus} onHover={onHover} onPick={onPick} refRow={repRef} colors={D.colors}
        cmp={repCmp} refId={D.refId} />
    );
    const brks = r.breaks && (
      <Matrix<Bk> units={brkUnits} rows={ids.map((id) => ({ id, label: label(id), cells: r.breaks![id].slice(0, -1) }))} metrics={brkMetrics}
        metric={query.get("bm") ?? "dur"} setMetric={mq("bm")} focus={focus} onHover={onHover} onPick={onPick} refRow={brkRef} colors={D.colors}
        cmp={brkCmp} refId={D.refId} />
    );
    const first = r.mode === "intervals" ? "reps" : "breaks";
    const t = tab ?? first;
    return (
      <section>
        <div class="head">
          <div class="seg">
            <button class={t === "reps" ? "on" : ""} onClick={() => setQuery({ tab: first === "reps" ? null : "reps" })}>Reps</button>
            <button class={t === "breaks" ? "on" : ""} onClick={() => setQuery({ tab: first === "breaks" ? null : "breaks" })}>Breaks</button>
          </div>
          <span class="sub">{t === "reps" ? `Columns = the ${D.refId ? "reference's" : "anchor session's"} reps; other sessions' reps are matched to them ${r.rep_summary && Object.values(r.rep_summary).some((x) => x.match === "index") ? "by number" : "by length"} (a rep only some have gets an “R1+” column); Δ time = time at the reference rep's distance − reference time`
            : "Break k = from the end of the rep in slot k to the next rep; HR drop = peak around the rep end − HR at 30 / 60 s"}</span>
        </div>
        {t === "reps" && <RepLadder r={r} D={D} ids={ids} pace={pace} focus={focus} onHover={onHover} onPick={onPick} />}
        {t === "reps" ? reps : brks}
      </section>
    );
  }
  return null;
}

// ---------------------------------------------------------------- rep ladder: one line per session across the reps
const niceStep = (span: number, target: number, steps: number[]) => steps.find((s) => span / s <= target) ?? steps[steps.length - 1];

/** Rep-by-rep pace (or speed) per session: faster up; reference dark, highlighted session on top. Reads "who was
 *  faster in which rep"; the ranking (RepRank) sits under the map, the matrix below has Mean / Δ ref columns. */
function RepLadder({ r, D, ids, pace, focus, onHover, onPick }: {
  r: CmpResult; D: DD; ids: string[]; pace: boolean; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(800);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(320, el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const slots = r.slots ?? [];
  const RD = repData(r, D, ids, pace);
  const { byTime, lines, med, refV, fmt, dtxt } = RD;
  pace = RD.pace;
  const all = [...lines.flatMap((l) => l.v), ...(med ?? [])].filter((x): x is number => x !== null);
  if (!all.length || slots.length < 1) return null;
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = Math.max((hi - lo) * 0.08, pace ? 2 : 0.1);
  lo -= pad; hi += pad;
  const H = 200, L = 46, R = 12, T = 8, Bm = 22;
  const x = (k: number) => L + 16 + (slots.length === 1 ? (W - L - R - 32) / 2 : (k * (W - L - R - 32)) / (slots.length - 1));
  // faster up: pace small = up, speed large = up
  const y = (v: number) => T + (pace ? (v - lo) / (hi - lo) : (hi - v) / (hi - lo)) * (H - T - Bm);
  const step = pace ? niceStep(hi - lo, 6, [1, 2, 5, 10, 15, 20, 30, 60, 120]) : niceStep(hi - lo, 6, [0.1, 0.2, 0.25, 0.5, 1, 2, 5]);
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
  const colorOf = (id: string) => (id === focus && D.many && id !== D.refId ? FOCUS_MANY : D.colors[id] ?? "#888");
  const order = [...lines.filter((l) => l.id !== D.refId && l.id !== focus), ...lines.filter((l) => l.id === D.refId && l.id !== focus), ...lines.filter((l) => l.id === focus)];
  const path = (v: N[]) => {
    let d = "", pen = false;
    // connect over missing slots (a session without the extra "R1+" rep, an unmatched rep): the dots show which reps exist
    v.forEach((p, k) => { if (p === null) return; d += `${pen ? "L" : "M"}${x(k).toFixed(1)},${y(p).toFixed(1)}`; pen = true; });
    return d;
  };
  return (
    <div class="ladder">
      <div class="ladder-chart" ref={box}>
        <div class="title">{byTime ? "Time per rep (no distance)" : pace ? "Pace per rep (min/km)" : "Speed per rep (km/h)"} <span class="muted">— faster is up; dots = one rep; hover for values</span></div>
        <svg width={W} height={H} onMouseLeave={() => onHover(null)}>
          {ticks.map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="grid" /><text x={L - 6} y={y(v) + 4} class="ax" text-anchor="end">{fmt(v)}</text></g>)}
          {slots.map((s, k) => <text key={k} x={x(k)} y={H - 6} class="ax" text-anchor="middle">{slotName(s, true)}</text>)}
          {med && <path d={path(med)} class="med" />}
          {order.map((l) => {
            const on = l.id === focus, isRef = l.id === D.refId;
            const c = colorOf(l.id), wdt = on ? 3 : isRef ? 2.4 : D.many ? 1.2 : 1.6;
            const a = r.activities[l.id];
            return (
              <g key={l.id} onMouseEnter={() => onHover(l.id)} onClick={() => onPick(l.id)} style={{ cursor: "pointer" }}>
                <path d={path(l.v)} fill="none" stroke="transparent" stroke-width={10} />
                {on && <path d={path(l.v)} fill="none" stroke="#fff" stroke-width={wdt + 3} />}
                <path d={path(l.v)} fill="none" stroke={c} stroke-width={wdt} stroke-opacity={D.many && !on && !isRef ? 0.8 : 1} />
                {l.v.map((p, k) => p === null ? null : (
                  <circle key={k} cx={x(k)} cy={y(p)} r={on || isRef ? 4 : 3} fill={c} stroke="#fff" stroke-width={1}>
                    <title>{`${a?.date ?? l.id} · ${slotName(slots[k])}: ${fmt(p)}${byTime ? "" : pace ? " /km" : " km/h"}${refV && refV[k] !== null && refV[k] !== undefined && !isRef ? ` (${dtxt(p - refV[k]!)} vs ${D.refId ? "reference" : "median"})` : ""}`}</title>
                  </circle>))}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

/** per-rep values of every session in the active metric + ranking by the mean difference to the reference */
function repData(r: CmpResult, D: DD, ids: string[], pace: boolean) {
  // sessions without distance: rep times instead (shorter = up, like pace)
  const byTime = !ids.some((id) => (r.reps?.[id] ?? []).some((c) => c?.pace_s_km));
  if (byTime) pace = true;
  const val = (c: any): N => (!c ? null : byTime ? c.time_s ?? null : pace ? c.pace_s_km ?? null : c.speed_kmh ?? null);
  const lines = ids.map((id) => ({ id, v: (r.reps?.[id] ?? []).map(val) }));
  const med = !D.refId && r.reps_ref ? (r.reps_ref as any[]).map(val) : null;
  // ranking: mean difference to the reference over the reps both have (s/km or km/h); the reference itself = 0
  const refV = D.refId ? lines.find((l) => l.id === D.refId)?.v ?? null : med;
  const rank = lines.map((l) => {
    const d: number[] = [];
    if (refV) l.v.forEach((p, k) => { if (p !== null && refV[k] !== null && refV[k] !== undefined) d.push(p - refV[k]!); });
    const own = l.v.filter((p): p is number => p !== null);
    return { id: l.id, n: d.length, d: d.length ? d.reduce((a, b) => a + b, 0) / d.length : null, mean: own.length ? own.reduce((a, b) => a + b, 0) / own.length : null };
  }).sort((a, b) => (a.d === null ? 1 : b.d === null ? -1 : pace ? a.d - b.d : b.d - a.d));
  const fmt = (v: number) => (pace ? dur(v) : v.toFixed(1));
  const dtxt = (d: number) => (byTime ? `${sgn(d)} s` : pace ? `${sgn(d)} s/km` : `${sgn(d, (v) => v.toFixed(1))} km/h`);
  const faster = (d: number) => (pace ? d < 0 : d > 0);
  return { byTime, pace, lines, med, refV, rank, fmt, dtxt, faster };
}

function RepRank({ r, D, RD, focus, onHover, onPick, compact }: {
  r: CmpResult; D: DD; RD: ReturnType<typeof repData>; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void; compact?: boolean;
}) {
  const { rank, fmt, dtxt, faster, byTime, pace } = RD;
  const maxD = Math.max(1e-9, ...rank.map((q) => Math.abs(q.d ?? 0)));
  const colorOf = (id: string) => (id === focus && D.many && id !== D.refId ? FOCUS_MANY : D.colors[id] ?? "#888");
  const what = byTime ? "rep time" : pace ? "rep pace" : "rep speed";
  return (
    <div class="ladder-rank">
      <div class="title">{compact ? `Who was faster: mean ${what}, vs the ${D.refId ? "reference" : "median"}` : `Mean vs the ${D.refId ? "reference" : "median"}`} <span class="muted">(reps both have)</span></div>
      <table>
        <tbody onMouseLeave={() => onHover(null)}>
          {rank.map((q) => (
            <tr key={q.id} class={q.id === focus ? "hl" : ""} onMouseEnter={() => onHover(q.id)} onClick={() => onPick(q.id)}>
              <td><i class="sw" style={{ background: colorOf(q.id) }} />{r.activities[q.id]?.date ?? q.id}{q.id === D.refId ? <span class="muted"> ref</span> : ""}</td>
              <td class="num">{q.mean === null ? "—" : fmt(q.mean)}</td>
              <td class="num dl" title={q.d === null ? "" : `${dtxt(q.d)} over ${q.n} rep${q.n === 1 ? "" : "s"} both have`}>{q.id === D.refId ? "" : q.d === null ? "—" : dtxt(q.d)}</td>
              <td class="bar">{q.d !== null && q.id !== D.refId && <span class={faster(q.d) ? "f" : "s"} style={{ width: `${(Math.abs(q.d) / maxD) * 50}%` }} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div class="note">bar: green = faster than the {D.refId ? "reference" : "median"}, red = slower{compact ? " · hover the charts for values at a point" : ""}</div>
    </div>
  );
}
