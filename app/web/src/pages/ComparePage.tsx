// Compare one session with one or many: ghost race along the route, moving / elapsed time, rep-aligned
// intervals (breaks squeezed to a fixed width) and break-aligned recoveries. Engine: scripts/compare.py
// via /api/compare; comparison sets via /api/compare/suggest. Everything is in the URL:
// /compare?ids=a,b,c&mode=distance|moving|elapsed|intervals|breaks&ref=first|median&focus=<id>
//   (no mode: the suggested mode of the first session, e.g. intervals for an interval set)
//   &metric=pace|speed&delta=moving|elapsed&rep_axis=absolute|normalized&gap=60&rgap=30&match=auto|index
//   &start=ref|<id>|lat,lon&srad=40   (common start line in Route position / Moving / Elapsed)
//   &start=rep1                       (t = 0 at rep 1 in Route position / Moving / Elapsed)
//   &rep=R3 (or 3)                    (Intervals: only that rep, own real-time axis; Breaks: only the break after it)
//   &view=reps                        (Intervals: one small chart per rep)
//   &smooth=raw                       (Intervals: 1 s pace / speed instead of the smoothed line)
// /compare?suggest=<id> picks the best suggested set for that activity and redirects.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useApi, qs, type Activity } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { useTheme } from "../lib/theme";
import { SPORT_LABEL, dur, num, stypeLabel, typeLabel, onFoot } from "../lib/format";
import { PageHeader } from "../components/PageHeader";
import { Table, type Col } from "../components/Table";
import { DistCell, distSort } from "../components/ActivityBits";
import { CompareCharts, type CPanel } from "../components/CompareCharts";
import { CompareMap } from "../components/CompareMap";
import { paceRange, speedRange } from "../lib/chartAxes";
import {
  MODES, buildGrid, interp, stats, colorsFor, sgn, kmhToPace, xLabel, alignedTicks, spans as spansOf, posAt, focusColor, slotName, repGap,
  sdur, secTxt, metres, gapTxt, gapTick, repFromUrl, repToUrl, smoothRep, pRange, breakName,
  type Mode, type CmpResult, type CmpSession, type Suggest, type SuggestSet, type AlignSession,
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
      <div class="head"><h2 title="Click a row to compare it with its suggested set (same workout, same route, most similar), or paste ids above. Also: the Compare button on any activity page.">Pick a session</h2>
        <input type="search" placeholder="Filter: 6×1000, Bislett, Holmenkollen, 2025…" value={q} style={{ width: "260px" }}
          onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
        <span class="count" title={q ? undefined : "the 40 most recent runs and ski sessions"}>{rows.length}</span></div>
      <Table rows={rows} rowKey={(a) => a.id} onRow={(a) => navigate(`/compare?suggest=${a.id}`)} empty={acts ? "No activity matches." : "Loading…"} cols={[
        { key: "start", label: "Date", render: (a) => a.date },
        { key: "type", label: "Workout", render: (a) => shortWhat(a) },
        { key: "place", label: "Place", render: (a) => a.place_name ?? a.place ?? a.location ?? "—" },
        { key: "dist_km", label: "km", num: true, render: (a) => <DistCell a={a} />, sort: distSort },
        { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s) },
        { key: "hr", label: "HR", num: true, render: (a) => num(a.hr) },
      ]} />
    </section>
  );
}

// ---------------------------------------------------------------- session picker
/** "late · joins 1.5 km in" / "lead-in 5:24 · 890 m" — the Start cell / chip badge for a session with a common start */
function startText(a: AlignSession | undefined, short = false): string {
  if (!a) return "";
  if (a.kind === "rep1") {
    if (a.status === "excluded") return short ? "excluded" : `excluded: ${a.reason}`;
    const at = `rep 1 at ${dur(a.offset_s ?? 0)} · ${(((a.offset_m ?? 0)) / 1000).toFixed(2)} km in`;
    if (a.status === "late") return short ? `from R${a.slot}` : `${a.reason}; its own rep ${a.rep_n} (${at.slice(6)})`;
    return short ? `R1 at ${dur(a.offset_s ?? 0)}` : (a.rep_n && a.rep_n > 1 ? `its rep ${a.rep_n} (after extra reps) = rep 1, ${at.slice(6)}` : at);
  }
  if (a.status === "anchor") return short ? "start" : "anchor (its start is the line)";
  if (a.status === "excluded") return short ? "excluded" : `excluded: ${a.reason}`;
  const jm = a.join_m ?? 0;
  if (a.status === "late") return short ? `late +${jm >= 1000 ? (jm / 1000).toFixed(1) + " km" : jm + " m"}` : `late: joins ${jm >= 1000 ? (jm / 1000).toFixed(2) + " km" : jm + " m"} after the line`;
  const lead = (a.offset_s ?? 0) >= 1 ? `lead-in ${dur(a.offset_s!)} · ${a.offset_m ?? 0} m` : "at its start";
  return lead + (a.zone_pause_s ? ` · left the line after a ${dur(a.zone_pause_s)} stop` : "");
}

function Picker({ ids, refKind, acts, colors, focus, sug, mode, anchor, align, canStart }: {
  ids: string[]; refKind: "first" | "median"; acts: Map<string, Activity>; colors: Record<string, string>; focus: string | null;
  sug: Suggest | null; mode: Mode;
  /** common start: the anchor session (null = each session from its own start), per-session status */
  anchor: string | null; align: Record<string, AlignSession> | null; canStart: boolean;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const set = (next: string[], extra: Record<string, string | null> = {}) => {
    // removing the start anchor: back to the (new) first session's start
    const st = anchor && !next.includes(anchor) ? { start: "ref" } : {};
    setQuery({ ids: next.slice(0, MAX_IDS).join(","), focus: next.includes(focus ?? "") ? focus : null, ...st, ...extra }, false);
  };
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
        {ids.map((id, k) => {
          const al = align?.[id];
          const wl = (acts.get(id) ?? sug?.activities[id])?.workout_label;
          return (
          <span key={id} class={"schip" + (id === focus ? " on" : "") + (al?.status === "excluded" ? " excl" : "")}
            title={`${label(id)}${wl ? " — " + wl : ""}${al ? `\nCommon start: ${startText(al)}` : ""}${al?.status === "excluded" ? "\n(start = use this session's start instead)" : ""} (click: highlight)`}
            onClick={() => setQuery({ focus: id === focus ? null : id })}>
            <i style={{ background: colors[id] ?? "#b9bfc6" }} />
            {label(id)}
            {k === 0 && refKind === "first" ? <b class="ref">ref</b> :
              refKind === "first" && <button class="x" title="Make this the reference" onClick={(e) => { e.stopPropagation(); set([id, ...ids.filter((x) => x !== id)]); }}>ref</button>}
            {canStart && (id === anchor ? <b class="flag" title="Common start: this session's start is the start line">start</b>
              : <button class="x" title="Use this start: every session starts where this one started (common start line)"
                onClick={(e) => { e.stopPropagation(); setQuery({ start: k === 0 ? "ref" : id }); }}>start</button>)}
            {al?.status === "late" && <b class="late" title={startText(al)}>{startText(al, true)}</b>}
            {al?.status === "excluded" && <b class="exbadge" title={startText(al)}>excluded</b>}
            <a class="x" href={`/activities/${id}`} title="Open activity" onClick={(e) => e.stopPropagation()}>↗</a>
            {ids.length > 1 && <button class="x" title="Remove" onClick={(e) => { e.stopPropagation(); set(ids.filter((x) => x !== id)); }}>×</button>}
          </span>
          );
        })}
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
  /** compare every cell with the reference's cell in the same column: the difference in the cell's tooltip,
   *  plus Mean and Δ ref columns */
  vsRef?: boolean; dfmt?: (d: number) => string }

function Matrix<R>({ units, rows, metrics, metric, setMetric, focus, onHover, onPick, refRow, colors, extraCols = [], cmp, refId, sel }: {
  units: MCol[]; rows: { id: string; label: any; cells: (R | null)[] }[]; metrics: Metric<R>[]; metric: string;
  setMetric: (m: string) => void; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void;
  refRow?: { label: string; cells: (R | null)[] } | null; colors: Record<string, string>;
  extraCols?: Col<{ id: string; label: any; cells: (R | null)[] }>[];
  /** the reference's cells (reference session or median) for the differences */
  cmp?: (R | null)[] | null; refId?: string | null;
  /** highlighted column (the selected rep / break) */
  sel?: number | null;
}) {
  const M = metrics.find((m) => m.id === metric) ?? metrics[0];
  type Row = { id: string; label: any; cells: (R | null)[] };
  const all: Row[] = refRow ? [{ id: "_ref", label: <span class="muted">{refRow.label}</span>, cells: refRow.cells }, ...rows] : rows;
  const num = (c: R | null | undefined): N => { if (!c) return null; const v = M.get(c); return typeof v === "number" ? v : null; };
  const best = units.map((_u, k) => bestOf(rows, (r) => { const c = r.cells[k]; return c && !(c as any).partial ? num(c) : null; }, M.best));
  const vs = !!(M.vsRef && cmp);
  const diff = (r: Row, k: number): N => {
    if (!vs || r.id === "_ref" || r.id === refId) return null;
    const v = num(r.cells[k]), w = num(cmp![k]);
    return v === null || w === null ? null : v - w;
  };
  const df = M.dfmt ?? ((d: number) => sgn(d, (x) => String(Math.round(x))));
  const mean = (r: Row) => { const v = r.cells.map(num).filter((x): x is number => x !== null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const meanDiff = (r: Row) => { const d = units.map((_u, k) => diff(r, k)).filter((x): x is number => x !== null); return d.length ? d.reduce((a, b) => a + b, 0) / d.length : null; };
  const bestMean = bestOf(rows, mean, M.best);
  const cols: Col<Row>[] = [
    { key: "label", label: "Session", render: (r) => <span><i class="sw" style={{ background: colors[r.id] ?? "transparent" }} />{r.label}</span>, sort: (r) => (r.id === "_ref" ? "" : String(r.id)) },
    ...units.map((u, k): Col<Row> => ({
      key: u.key, label: u.label, title: u.title, num: true, cls: k === sel ? "selcol" : undefined,
      sort: (r) => { const c = r.cells[k]; const v = c ? M.get(c) : null; return typeof v === "number" ? v : v ?? null; },
      render: (r) => {
        const c = r.cells[k];
        if (!c) return <span class="muted">—</span>;
        const v = M.get(c);
        const part = (c as any).partial;
        const d = diff(r, k);
        return <span class={(r.id !== "_ref" && typeof v === "number" && v === best[k] ? "best" : "") + (part ? " partial" : "")}
          title={part ? `partial: ${Math.round(part * 100)} % of the leg` : d !== null ? `${df(d)} vs the ${refId ? "reference" : "median"}` : undefined}>
          {v === null || v === undefined ? "—" : M.fmt(v, c)}</span>;
      },
    })),
    ...(vs ? [
      { key: "_mean", label: "Mean", num: true, title: "mean over this session's reps", sort: (r: Row) => mean(r),
        render: (r: Row) => { const m = mean(r); return m === null ? "—" : <span class={m === bestMean && r.id !== "_ref" ? "best" : ""}>{M.fmt(m, r.cells.find(Boolean) as R)}</span>; } },
      { key: "_d", label: "Δ ref", num: true, title: "mean difference to the reference over the reps both have", sort: (r: Row) => meanDiff(r),
        render: (r: Row) => { const d = meanDiff(r); return d === null ? <span class="muted">{r.id === refId ? "ref" : "—"}</span> : df(d); } },
    ] as Col<Row>[] : []),
    ...extraCols,
  ];
  return (
    <div>
      <div class="head sm">
        <div class="seg" title={[M.best ? `bold = best per column (${M.best === "min" ? "lowest" : "highest"})` : "", vs ? `hover a cell for the difference to the ${refId ? "reference" : "median"}` : ""].filter(Boolean).join("; ") || undefined}>
          {metrics.map((m) => <button key={m.id} class={m.id === M.id ? "on" : ""} onClick={() => setMetric(m.id)}>{m.label}</button>)}</div>
      </div>
      <Table rows={all} rowKey={(r) => r.id} cols={cols} highlight={focus} onHover={(r) => onHover(r && r.id !== "_ref" ? r.id : null)}
        onRow={(r) => r.id !== "_ref" && onPick(r.id)} />
    </div>
  );
}

// ---------------------------------------------------------------- page
export function ComparePage() {
  const { query } = useLocation();
  const theme = useTheme();
  const ids = parseIds(query.get("ids") ?? "");
  const suggestId = query.get("suggest");
  const modeQ = MODES.some((m) => m.id === query.get("mode")) ? (query.get("mode") as Mode) : null;
  const refKind = query.get("ref") === "median" ? "median" : "first";
  const repAxis = query.get("rep_axis") === "normalized" ? "normalized" : "absolute";
  const gap = query.get("gap") ?? "60", rgap = query.get("rgap") ?? "30";
  const match = query.get("match") === "index" ? "index" : "auto";
  const deltaKind = query.get("delta") === "elapsed" ? "elapsed" : "moving";
  const pinned = query.get("focus");
  // common start line: ref = the first session's start, <id> = that session's start (an id no longer in the set = ref)
  const startRaw = query.get("start");
  const startQ = startRaw && (startRaw === "ref" || startRaw === "rep1" || /^-?\d+(\.\d+)?,-?\d+(\.\d+)?$/.test(startRaw) || ids.includes(startRaw)) ? startRaw : startRaw ? "ref" : null;
  const rep1 = startQ === "rep1";
  const srad = query.get("srad");
  const tab = query.get("tab");
  const [hover, setHover] = useState<string | null>(null);
  const [cur, setCur] = useState<number | null>(null);
  // per-rep small multiples: cursor in slot k at grid index i (map markers)
  const [rc, setRc] = useState<{ k: number; i: number } | null>(null);
  const focus = hover ?? pinned;

  const acts = useApi<{ activities: Activity[] }>("/api/activities").data;
  const actMap = useMemo(() => new Map((acts?.activities ?? []).map((a) => [a.id, a])), [acts]);
  const sug = useApi<Suggest>(suggestId ? `/api/compare/suggest?id=${suggestId}` : ids[0] ? `/api/compare/suggest?id=${ids[0]}` : null);
  // the suggest answer for the first session (null while loading, with no ids, or for another session)
  const sugFirst = ids[0] && sug.data?.id === ids[0] ? sug.data : null;
  // no mode in the URL: the first session's suggested mode (intervals for an interval set), Route position otherwise.
  // Wait for the suggest call so the page doesn't compute Route position first and then switch.
  const modeWait = !modeQ && !!ids[0] && !sugFirst && !sug.error;
  const sugMode = sugFirst && (!sugFirst.modes || sugFirst.modes.includes(sugFirst.mode)) ? sugFirst.mode : null;
  const mode: Mode = modeQ ?? sugMode ?? (sugFirst?.modes && !sugFirst.modes.includes("distance") ? sugFirst.modes[0] ?? "moving" : "distance");

  useEffect(() => {
    if (!suggestId) return;
    if (sug.data) {
      if (sug.data.comparable === false) return;   // the page says why
      const s = pickSet(sug.data);
      navigate(`/compare${qs({ ids: s ? s.ids.join(",") : suggestId, mode: s?.mode ?? sug.data.mode })}`, true);
    } else if (sug.error) navigate(`/compare${qs({ ids: suggestId, mode: "moving" })}`, true);
  }, [suggestId, sug.data, sug.error]);

  const iv = mode === "intervals" || mode === "breaks";
  // one rep (Intervals) / the break after it (Breaks), by slot label; one small chart per rep (Intervals)
  const perRep = mode === "intervals" && query.get("view") === "reps";
  // rep=R3 (as the UI labels it) or rep=3 -> slot label "3"
  const repSel = iv && !perRep ? repFromUrl(query.get("rep")) : null;
  const rawRep = query.get("smooth") === "raw";
  // modes the engine can run for the first session (from the suggest call); distance modes 422 without distance
  const allowed = sugFirst?.modes ?? null;
  // Start: Rep 1 is offered when the first session is an interval session (or it's on already)
  const hasReps = !!(actMap.get(ids[0])?.sig || sugFirst?.mode === "intervals");
  const url = ids.length && !suggestId && !modeWait ? `/api/compare${qs({
    ids: ids.join(","), mode, reference: refKind,
    rep_axis: mode === "intervals" && repAxis !== "absolute" ? repAxis : null,
    gap_s: mode === "intervals" && gap !== "60" ? gap : null, rep_gap_s: mode === "breaks" && rgap !== "30" ? rgap : null,
    match: (iv || rep1) && match !== "auto" ? match : null,
    start: !iv && startQ ? startQ : null, start_radius_m: !iv && startQ && !rep1 && srad ? srad : null,
    rep_series: mode === "intervals" && (repSel || perRep) ? 1 : null,
  })}` : null;
  const { data: r, error, loading } = useApi<CmpResult>(url);
  useEffect(() => { document.title = "Compare"; }, []);
  useEffect(() => { setCur(null); setRc(null); }, [r, repSel, perRep]);

  const firstSport = (r?.sessions[0]?.sport ?? actMap.get(ids[0])?.sport) || "run";
  // pace for on-foot sports (run, hike), km/h for ski, bike and the rest; breaks view: speed only (pace is
  // meaningless while standing / walking)
  const defMetric = onFoot(firstSport) ? "pace" : "speed";
  const tableMetric = (query.get("metric") as "pace" | "speed") || defMetric;
  const metric = mode === "breaks" ? "speed" : tableMetric;

  // ---- derived chart data
  const D = useMemo<DD | null>(() => {
    if (!r || !r.series || !Object.keys(r.series).length) return null;
    const sids = r.ids.map(String).filter((i) => r.series[i]);
    // the reference may have no series in this mode (e.g. a pool swim without reps in Intervals): fall back to the engine's
    // anchor session, else no reference line (gap vs the median)
    const ref0 = r.reference.kind === "session" ? String(r.reference.id) : null;
    const anchor = r.anchor_id;
    const refId = ref0 && r.series[ref0] ? ref0 : ref0 && anchor != null && r.series[String(anchor)] ? String(anchor) : null;
    const { colors, many } = colorsFor(sids, refId, theme);
    const fc = focusColor(theme);
    const mapColors = colorsFor(sids, refId).colors; // basemap tiles stay light
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
      // intervals: the rep-chart smoothing (rolling median + mean) unless smooth=raw
      speed[id] = mode === "intervals" ? (rawRep ? raw[id] : smoothRep(raw[id])) : sk ? smoothN(raw[id], sk) : raw[id];
      hr[id] = sk && !iv ? smoothN(g(id, "hr"), sk) : g(id, "hr");
      if (mode === "distance") {
        delta[id] = g(id, deltaKind === "moving" ? "delta_moving_s" : "delta_elapsed_s");
        // t = the file's elapsed time at x (map); elapsed_s counts from the common start line when one is set
        t[id] = g(id, r.series[id].t ? "t" : "elapsed_s"); mov[id] = g(id, deltaKind === "moving" ? "moving_s" : "elapsed_s");
        if (!r.route_ref) alt[id] = g(id, "alt");
      } else if (mode === "moving" || mode === "elapsed") {
        delta[id] = g(id, "ahead_m"); t[id] = g(id, "t"); alt[id] = g(id, "alt");
      } else t[id] = g(id, "t");
    }
    const routeAlt = r.route_ref ? interp(r.route_ref.m, r.route_ref.alt, X, 1e9) : null;
    return { sids, refId, colors, mapColors, many, fc, order, X, speed, raw, repSpeed, hr, alt, delta, t, mov, routeAlt };
  }, [r, deltaKind, iv, theme, rawRep]);

  // ---- one rep / break selected (rep=<label>): Intervals = that rep on its own real-time axis (rep_series, lead-in
  // and the start of the next break included); Breaks = the stretch of that slot (squeezed rep + break) cut out
  const selK = useMemo(() => (r?.slots && repSel ? r.slots.findIndex((s) => s.label === repSel) : -1), [r, repSel]);
  const repView = mode === "intervals" && selK >= 0 && !!r?.rep_series?.slots[selK];
  const V = useMemo<DD | null>(() => {
    if (!D || !r || selK < 0) return D;
    if (repView) return repViewD(r, D, selK, rawRep);
    if (mode === "breaks" && r.axis?.break_x0?.[selK] !== undefined && r.axis.break_width[selK] > 0)
      return sliceD(D, r.axis.rep_x0[selK], r.axis.break_x0[selK] + r.axis.break_width[selK]);
    return D;
  }, [D, r, selK, repView, mode, rawRep]);
  const repW = repView ? r!.rep_series!.slots[selK].width : 0;

  const panels = useMemo<CPanel[]>(() => {
    const D = V;
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
      P.push(mk("delta", `Gap to the ${refKind === "median" ? "median" : "reference"} (${deltaKind} time; up = ahead)`, D.delta,
        gapTick(secTxt), 160, { zero: true, dir: -1, axisSize: 84 }));
      P.push(speedP, hrP);
      if (D.routeAlt) P.push({ key: "alt", label: "Elevation along the route (m)", ys: {}, single: D.routeAlt, fmt: (v) => String(Math.round(v)), height: 90 });
      else P.push(mk("alt", "Elevation (m)", D.alt, (v) => String(Math.round(v)), 90));
    } else if (mode === "moving" || mode === "elapsed") {
      P.push(speedP, hrP);
      P.push(mk("delta", `Distance to the ${refKind === "median" ? "median" : "reference"} at the same ${mode} time (up = ahead)`, D.delta,
        gapTick(metres, "ahead"), 140, { zero: true, axisSize: 84 }));
      P.push(mk("alt", "Elevation (m)", D.alt, (v) => String(Math.round(v)), 90));
    } else if (mode === "intervals") {
      // y range of pace / speed from the reps only (breaks would squash them)
      const sp = repView ? [{ a: 0, b: repW }] : r ? spansOf(r) : [];
      speedP.rangeMask = D.X.map((x) => sp.some((q) => x >= q.a + 10 && x <= q.b));
      const has = (ys: Record<string, N[]>) => Object.values(ys).some((a) => a.some((v) => v !== null));
      if (has(speedP.ys)) P.push(speedP);   // sessions without distance: HR only
      // gap inside each rep (seconds at the same distance): the clearest "who is faster in this rep, by how much"
      const x0 = repView ? [0] : r.axis?.slot_x0, w = repView ? [repW] : r.axis?.slot_width;
      if ((repView || r.axis?.rep_axis !== "normalized") && x0 && D.sids.length >= 2) {   // one session: nothing to gap against
        const refSp = D.refId ? D.repSpeed[D.refId] : stats(D.sids.map((i) => D.repSpeed[i])).med;
        const gaps: Record<string, N[]> = {};
        for (const i of D.sids) gaps[i] = repGap(D.X, x0, w, D.repSpeed[i], refSp);
        if (has(gaps)) P.push(mk("gap", `Gap within ${repView ? "the" : "each"} rep to the ${refKind === "median" ? "median" : "reference"} (time at the same distance; up = ahead)`, gaps,
          gapTick(secTxt), 130, { zero: true, dir: -1, axis: "num", axisSize: 84 }));
      }
      P.push(hrP);
    }
    else { P.push(hrP); if (Object.values(speedP.ys).some((a) => a.some((v) => v !== null))) P.push(speedP); }
    // drop panels without any data (pool swims: no HR / elevation) instead of an empty chart with a 0–1 axis
    return P.filter((p) => p.single || Object.values(p.ys).some((a) => a.some((v) => v !== null && v !== undefined)));
  }, [V, metric, refKind, mode, repView, repW]);

  const ticks = useMemo(() => (r && !repView ? alignedTicks(r) : null), [r, repView]);
  const sp = useMemo(() => (repView ? [{ a: 0, b: repW }] : r ? spansOf(r) : []), [r, repView, repW]);
  const xFmt = (x: number) => (mode === "distance" ? `${(x / 1000).toFixed(x % 1000 ? 1 : 0)} km` : dur(x));
  const selSlot = selK >= 0 ? r?.slots?.[selK] : undefined;
  // cursor label in the one-rep view: seconds since the rep start (before / after it in real time)
  const repXl = repView && selSlot ? (x: number) => (x < 0 ? `before ${slotName(selSlot)} · ${sdur(x)}` : x <= repW ? `${slotName(selSlot)} · ${dur(x)}`
    : `after ${slotName(selSlot)} · +${dur(x - repW)} (break, real time)`) : undefined;
  // map: only the selected rep's (or break's) stretch of every session drawn fully, fitted to it
  const win = useMemo(() => {
    if (!r || selK < 0) return null;
    const o: Record<string, [number, number]> = {};
    for (const id of r.ids.map(String)) {
      if (mode === "intervals") { const q = r.reps?.[id]?.[selK]; if (q) o[id] = [q.start_s, q.start_s + q.time_s]; }
      else { const b = r.breaks?.[id]?.[selK]; if (b) o[id] = [b.start_s, b.start_s + b.dur_s]; }
    }
    return o;
  }, [r, selK, mode]);

  // ---- positions at the cursor. distance mode: ghost race = where each session was when the reference
  // (or the median) reached x, in moving or elapsed time
  const positions = useMemo(() => {
    const out: Record<string, [number, number] | null> = {};
    if (perRep && rc && r?.tracks && r.rep_series) {   // small multiples: everyone at the same second of that rep
      const S = r.rep_series.slots[rc.k];
      for (const [id, q] of Object.entries(S?.series ?? {})) out[id] = posAt(r.tracks[id], q.start_s + S.x0 + rc.i);
      return out;
    }
    if (!D || !V || !r?.tracks || cur === null) return out;
    if (mode === "distance") {
      // times count from where each session starts on the axis (its first bin), so a session that joins the
      // route late (or after a lead-in) starts moving with the reference instead of waiting at its start
      const first = (a: N[]) => { for (const v of a) if (v !== null) return v; return null; };
      const refT = D.refId ? D.mov[D.refId][cur] : r.median?.moving_s ? interp(r.median.x as number[], r.median.moving_s, [D.X[cur]], 1e9)[0] : null;
      const ref0 = D.refId ? first(D.mov[D.refId]) : 0;
      const T0 = refT !== null && refT !== undefined && ref0 !== null ? refT - ref0 : null;
      for (const id of D.sids) {
        const m = D.mov[id], te = D.t[id];
        if (T0 === null) { out[id] = posAt(r.tracks[id], te[cur]); continue; }
        const m0 = first(m);
        // first bin where this session's time since its start >= T0 -> its elapsed time there
        let k = -1;
        if (m0 !== null) for (let i = 0; i < m.length; i++) if (m[i] !== null && m[i]! - m0 >= T0) { k = i; break; }
        out[id] = k >= 0 ? posAt(r.tracks[id], te[k]) : null;
      }
      return out;
    }
    for (const id of V.sids) out[id] = posAt(r.tracks[id], V.t[id][cur]);
    return out;
  }, [cur, D, V, r, mode, perRep, rc]);

  const pick = (id: string) => setQuery({ focus: id === pinned ? null : id });
  // common start: each track's lead-in (before its start on the line) is drawn dimmed on the map
  const cut = useMemo(() => {
    const o: Record<string, number> = {};
    for (const [id, a] of Object.entries(r?.align?.applied ? r.align.sessions ?? {} : {})) if (a.offset_s) o[id] = a.offset_s;
    return o;
  }, [r]);

  // ---- render
  if (suggestId) return <main><div class="empty">{sug.error ? sug.error
    : sug.data?.comparable === false ? <>This activity can't be compared: {sug.data.why_not ?? "no usable data"}. <a href={`/activities/${suggestId}`}>Back to the activity</a></>
    : "Finding sessions to compare…"}</div></main>;
  const opts: Opts = { mode, iv, perRep, repOne: !!repSel, refKind, startQ, rep1, hasReps, metric: tableMetric, defMetric, deltaKind,
    repAxis, gap, rgap, match, rawRep, startName: startQ && ids.includes(startQ) && startQ !== ids[0] ? (actMap.get(startQ)?.date ?? startQ) : null };
  const head = (
    <section class="first">
      <PageHeader title="Compare" sub={<>{ids.length ? `${ids.length} session${ids.length > 1 ? "s" : ""}` : "one session vs one or many"}{loading || modeWait ? " · loading…" : ""}</>}
        about="Line up sessions of the same workout or route and see where you gained or lost time."
        tips={[
          "No ids yet? Click a session below: it opens with its suggested set (same workout, same route, most similar). Suggestions ▾ swaps or adds sessions.",
          "Route position = ghost race along the route; Intervals = reps lined up on their start (All, Per rep or one rep, e.g. R3); Breaks = the recoveries.",
          "Hover a chart, a track, a chip or a table row to highlight a session, click to keep it. Drag on a chart to zoom, double-click to reset.",
          "Options: reference (first session or median), common start or t = 0 at rep 1, pace or speed, rep matching. Changed options show as chips; × resets one.",
        ]} />
      <Picker ids={ids} refKind={refKind} acts={actMap} colors={D?.colors ?? {}} focus={pinned} sug={sug.data} mode={mode}
        anchor={!iv && startQ && !rep1 ? (startQ === "ref" || startQ.includes(",") ? ids[0] : startQ) : null}
        align={r?.align?.applied ? r.align.sessions ?? null : null} canStart={!iv} />
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
          {iv && r?.slots && r.mode === mode && <RepPick r={r} mode={mode} sel={repSel} perRep={perRep} />}
          <OptionsBar o={opts} />
        </div>
      )}
      {r && <ModeNote r={r} />}
    </section>
  );

  if (!ids.length) return <main class="cmp">{head}<Landing /></main>;
  if (error) return <main class="cmp">{head}<div class="err">{error}{iv ? " — reps exist only for runs classified as intervals and ski interval sessions; try Moving time or Route position."
    : allowed && !allowed.includes(mode) ? " — the first session has no distance; try Moving time, Elapsed or Intervals." : ""}</div></main>;
  if (!r || !D || !V) return <main class="cmp">{head}<div class="empty">{loading ? "Comparing…" : "Nothing to show."}</div></main>;
  const sel = selK >= 0 ? selK : null;

  return (
    <main class="cmp">
      {head}
      <section>
        <div class="cmp-top">
          <div class="cmp-left">
            <div class="cmp-map">
              <CompareMap tracks={r.tracks ?? {}} order={D.order} colors={D.mapColors} refId={D.refId} focus={focus} many={D.many}
                positions={positions} routeRef={r.route_ref} onPick={setHover} onClick={pick} cut={cut} win={win} />
            </div>
            <CursorLegend r={r} D={V} panels={panels} cur={perRep ? null : cur} focus={focus} onHover={setHover} onPick={pick} pace={tableMetric === "pace"} xl={repXl}
              sel={repView ? selK : null} />
          </div>
          <div class="cmp-right">
            {perRep ? <PerRep r={r} D={D} metric={metric} focus={focus} onHover={setHover} onCursor={setRc} raw={rawRep} />
              : <CompareCharts X={V.X} panels={panels} order={V.order} colors={V.colors} refId={V.refId} focus={focus} many={V.many}
              spans={sp} ticks={ticks} xFmt={repView ? sdur : xFmt} xIncrs={mode === "distance" ? [100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000] : repView ? [5, 10, 15, 30, 60, 120, 300, 600] : [30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200]} onCursor={setCur} onPick={setHover}
              names={Object.fromEntries(V.sids.map((i) => [i, r.activities[i]?.date ?? i]))} />}
            <div class="note">{perRep ? `One chart per rep, x = seconds since the rep start (${r.rep_series?.lead_s ?? 15} s before it, up to ${r.rep_series?.post_s ?? 30} s of the next break after it), every session overlaid, same ${metric} scale in all. Click a chart to open that rep.`
              : repView && selSlot ? `${slotName(selSlot)} only: x = seconds since each session's ${selSlot.anchor ? "" : "extra "}rep start, in real time (${r.rep_series!.lead_s} s before it and up to ${r.rep_series!.post_s} s of the next break after it; shaded = the longest rep). `
              : mode === "breaks" && sel !== null && V !== D ? `Break after ${selSlot ? slotName(selSlot) : ""} only. ` : ""}
              {D.many && !perRep ? "Grey band = middle 50 % of the sessions." : ""}</div>
          </div>
        </div>
      </section>
      {iv && <Detail r={r} D={D} focus={focus} onHover={setHover} onPick={pick} tab={tab} metric={tableMetric} sel={sel} />}
      <section>
        <div class="head"><h2 title={`${iv ? "reps summary first, then the whole session; " : ""}${r.align?.applied ? (r.align.start?.kind === "rep1" ? "totals from the start of rep 1 (hover for the whole session); " : "totals from the common start line (hover for the whole session); ") : ""}deltas vs the ${refKind === "median" ? "median of the selection" : "reference (first session)"}; bold = best per column`}>Sessions</h2></div>
        <SummaryTable r={r} D={D} focus={focus} onHover={setHover} onPick={pick} deltaKind={deltaKind} metric={tableMetric} />
      </section>
      {!iv && <Detail r={r} D={D} focus={focus} onHover={setHover} onPick={pick} tab={tab} metric={tableMetric} />}
    </main>
  );
}

function ModeNote({ r }: { r: CmpResult }) {
  const a = r.axis || {};
  const bits: string[] = [];
  if (r.mode === "distance" && a.t0 !== "rep1") bits.push(a.route ? `x = position along route ${a.route} (${a.route_name}); markers on the map: where everyone was when the ${r.reference.kind === "median" ? "median" : "reference"} reached the cursor` : "x = own distance (sessions don't share one compared route)");
  if (r.mode === "moving") bits.push("x = moving time; markers = everyone at the same moving time");
  if (r.mode === "elapsed") bits.push("x = elapsed time; markers = everyone at the same clock time");
  if (r.mode === "intervals") bits.push(`reps start at the same x; breaks squeezed to ${a.gap_s} s; ${a.rep_axis === "normalized" ? "x within a rep = % of the rep" : "x within a rep = seconds since its start"}`);
  if (r.mode === "breaks") bits.push(`breaks start at the same x (real seconds); reps squeezed to ${a.rep_gap_s} s; Recovery = up to 2 min after the last rep`);
  if ((r.mode === "intervals" || r.mode === "breaks") && r.sessions.some((s) => s.sport === "ski"))
    bits.push("ski: reps = the hard efforts between stops (laps or climbs found by ski_intervals), so reps of different sessions can be on different parts of a climb");
  const al = r.align;
  const ex = new Set((al?.applied ? al.excluded ?? [] : []).map((e) => e.id));
  if (al?.applied && al.start?.kind === "rep1") {
    const date = (id: number) => r.activities[String(id)]?.date ?? String(id);
    const late = al.late ?? [];
    bits.push(`t = 0 at rep 1: every session from the detected start of its rep 1 (matched to rep 1 of ${date(al.start.anchor_id)}, as in Intervals); warm-up cut off, real breaks kept`
      + (r.mode === "distance" ? "; x = own distance since rep 1" : "")
      + (late.length ? `; ${late.length} without a rep 1 ${late.length === 1 ? "enters" : "enter"} at the anchor's time of their first matched rep: ${late.map((i) => `${date(i)} (R${al.sessions?.[String(i)]?.slot})`).join(", ")}` : ""));
  } else if (al?.applied && al.start) {
    const date = (id: number) => r.activities[String(id)]?.date ?? String(id);
    const late = al.late ?? [];
    bits.push(`common start at the ${al.start.kind === "point" ? "point" : "start"} of ${date(al.start.anchor_id)} (line ±${Math.round(al.start.radius_m ?? 0)} m${al.start.route_m ? `, route ${(al.start.route_m / 1000).toFixed(2)} km` : ""}); x = 0 at the line, lead-ins cut off`
      + (late.length ? `; ${late.length} late (joined further along): ${late.map((i) => `${date(i)} +${al.sessions?.[String(i)]?.join_m} m`).join(", ")}` : ""));
  } else if (al && !al.applied && al.note) bits.push(al.note);
  if (ex.size) bits.push(`${ex.size} left out by the ${al?.start?.kind === "rep1" ? "rep 1 start" : "common start"}: ${(al!.excluded ?? []).map((e) => `${r.activities[String(e.id)]?.date ?? e.id} ${e.reason}`).join("; ")}`);
  const sk = r.skipped.filter((s) => !ex.has(s.id));
  if (sk.length) bits.push(`skipped: ${sk.map((s) => `${s.id} (${s.why})`).join(", ")}`);
  if (r.no_reps?.length) bits.push(`no reps in ${r.no_reps.join(", ")}`);
  const sports = [...new Set(r.sessions.map((s) => s.sport))];
  return <>
    <div class="note">{bits.join(" · ")}</div>
    {sports.length > 1 && <div class="note cmp-warn">Mixed sports ({sports.map((x) => SPORT_LABEL[x] ?? x).join(", ")}): paces, speeds and efficiency are not comparable across sports.</div>}
  </>;
}

/** chart data derived from one /api/compare result: every session on the shared x grid */
interface DD {
  sids: string[]; refId: string | null; colors: Record<string, string>; mapColors: Record<string, string>; many: boolean;
  /** colour of the highlighted session when the others are grey */
  fc: string; order: string[]; X: number[];
  speed: Record<string, N[]>; raw: Record<string, N[]>; repSpeed: Record<string, N[]>; hr: Record<string, N[]>; alt: Record<string, N[]>; delta: Record<string, N[]>; t: Record<string, N[]>;
  mov: Record<string, N[]>; routeAlt: N[] | null;
}

// ---------------------------------------------------------------- cursor legend (hover values per session)
function CursorLegend({ r, D, panels, cur, focus, onHover, onPick, pace, xl, sel = null }: {
  r: CmpResult; D: DD; panels: CPanel[]; cur: number | null; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void;
  pace?: boolean;
  /** the selected rep (slot index): "who was faster" in that rep only */
  sel?: number | null;
  /** cursor label (default: xLabel for the mode) */
  xl?: (x: number) => string;
}) {
  if (cur === null && r.mode === "intervals" && r.reps && pace !== undefined) {
    const ids = D.sids.filter((i) => r.reps![i]);
    return <RepRank r={r} D={D} RD={repData(r, D, ids, pace, sel)} focus={focus} onHover={onHover} onPick={onPick} compact
      slot={sel !== null && r.slots?.[sel] ? slotName(r.slots[sel]) : null} />;
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
      <div class="lh"><b>{xl ? xl(x) : xLabel(r, x)}</b>{alt !== null && alt !== undefined ? <span class="muted"> · {Math.round(alt)} m</span> : null}</div>
      <table>
        <thead><tr><th>Session</th>{P.map((p) => <th key={p.key} class="num">{p.key === "delta" || p.key === "gap" ? "Gap" : p.key === "hr" ? "HR" : p.label.split(" ")[0]}</th>)}</tr></thead>
        <tbody onMouseLeave={() => onHover(null)}>
          {rows.map(({ id, v }) => {
            const a = r.activities[id];
            return (
              <tr key={id} class={id === focus ? "hl" : ""} onMouseEnter={() => onHover(id)} onClick={() => onPick(id)}>
                <td><i class="sw" style={{ background: id === focus && D.many && id !== D.refId ? D.fc : D.colors[id] }} />{a?.date ?? id}{id === D.refId ? <span class="muted"> ref</span> : ""}</td>
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
      const ge = s.gap_end;   // engine: gap at the last position both this session and the reference covered
      if (ge) fin = deltaKind === "moving" ? ge.delta_moving_s : ge.delta_elapsed_s;
      else {
        const d = r.series[sid]?.[deltaKind === "moving" ? "delta_moving_s" : "delta_elapsed_s"] as N[] | undefined;
        if (d) for (let i = d.length - 1; i >= 0; i--) if (d[i] !== null) { fin = d[i]; break; }
      }
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
    <span><i class="sw" style={{ background: x.sid === focus && D.many && x.sid !== D.refId ? D.fc : D.colors[x.sid] }} />
      <a href={`/activities/${x.sid}`} onClick={(e) => e.stopPropagation()}>{x.date}</a>{x.sid === D.refId ? <span class="muted"> ref</span> : ""}</span>) });
  cols.push({ key: "what", label: "Workout", sort: (x) => x.sig ?? x.type ?? "", render: (x) => <span class="muted" title={x.a?.workout_label ?? ""}>{x.sig || shortWhat(x.a) || stypeLabel(x.type)}</span> });
  if (iv) {
    // interval modes: the rep summary first, the whole session after it
    const pk = (x: Row) => x.rs?.rep_pace_s_km as N | undefined;
    add("n_reps", "Reps", (x) => x.rs?.n_reps, (v) => String(v), null);
    if (pace) add("rep_pace", "Avg rep pace", pk, (v) => dur(v), "min", "avg rep pace = total rep distance / total rep time (min/km)");
    else add("rep_kmh", "Avg rep km/h", (x) => toKmh(pk(x)), (v) => v.toFixed(1), "max", "avg rep speed = total rep distance / total rep time");
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
    return <Table rows={rows} rowKey={(x) => x.sid} cols={cols} highlight={focus} onHover={(x) => onHover(x ? x.sid : null)} onRow={(x) => onPick(x.sid)} />;
  }
  // deltas vs the reference: own columns for time and pace, tooltips for the rest
  const dTip = (get: (x: Row) => N | undefined, f: (v: N) => string, what: string) => (x: Row) =>
    x.sid === D.refId ? "reference" : `${f(get(x) ?? null)} ${what} vs the ${refRow ? "reference" : "median"}`;
  const al = r.align?.applied ? r.align.sessions ?? {} : null;
  const wTip = (k: "dist_m" | "moving_s" | "elapsed_s" | "pause_s") => al ? (x: Row) => {
    const w = x.whole?.[k];
    return w === null || w === undefined ? "" : `whole session: ${k === "dist_m" ? `${(w / 1000).toFixed(2)} km` : dur(w)}`;
  } : undefined;
  const isRep1 = r.align?.start?.kind === "rep1";
  if (al) cols.push({ key: "start", label: isRep1 ? "Rep 1" : "Start", title: isRep1 ? "t = 0 at rep 1: where rep 1 started (elapsed time · distance into the session); before it is cut off. from R2 = no rep 1, enters at the anchor's time of its first matched rep"
    : "common start line: lead-in cut off before the line, late = joined further along the start session's path",
    sort: (x) => al[x.sid]?.offset_s ?? null,
    render: (x) => { const a = al[x.sid]; return <span class={"muted" + (a?.status === "late" ? " cmp-late" : "")} title={startText(a)}>{a?.status === "anchor" && !isRep1 ? "start" : isRep1 && a?.status !== "late" ? startText(a).replace(/^rep 1 at /, "") : startText(a, a?.status === "late")}</span>; } });
  add("dist_m", "km", (x) => x.dist_m, (v) => (v / 1000).toFixed(2), null, undefined, wTip("dist_m"));
  if (r.mode === "distance") {
    add("fin", "Gap at end", (x) => x.fin, (v) => gapTxt(v, secTxt), "min", `${deltaKind} time behind / ahead of the reference at the last point both covered`,
      (x) => (x.gap_end ? `at ${(x.gap_end.x_m / 1000).toFixed(1)} km, the last point this session and the reference both covered` : ""));
    if (r.axis?.x === "route_m") add("cover", "Covered", (x) => x.coverage_pct, (v, x) => `${Math.round(v)} %${x.reversed ? " rev." : ""}`, null,
      "share of the route this session ran on the route (jumps, shortcuts, detours and long off-route stretches don't count; the gap is held there). rev. = ran the route in the other direction",
      (x) => (x.uncovered?.length ? `not covered: ${x.uncovered.map(([a, b]) => `${(a / 1000).toFixed(1)}–${(b / 1000).toFixed(1)} km`).join(", ")}` : x.reversed ? "reversed: x is its own travel direction" : ""));
  }
  add("moving_s", "Moving", (x) => x.moving_s, (v) => dur(v), null, undefined, wTip("moving_s"));
  add("d_moving", "Δ", (x) => x.delta.moving_s, (v) => sgn(v, dur), null, "moving time vs reference");
  add("elapsed_s", "Elapsed", (x) => x.elapsed_s, (v) => dur(v), null, undefined, wTip("elapsed_s"));
  add("pause_s", "Stopped", (x) => x.pause_s, (v, x) => `${dur(v)}${x.n_pauses ? ` (${x.n_pauses})` : ""}`, null, "paused time (number of pauses)", wTip("pause_s"));
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
  return <Table rows={rows} rowKey={(x) => x.sid} cols={cols} highlight={focus} onHover={(x) => onHover(x ? x.sid : null)} onRow={(x) => onPick(x.sid)} />;
}

// ---------------------------------------------------------------- legs / reps / breaks
function Detail({ r, D, focus, onHover, onPick, tab, metric, sel = null }: {
  r: CmpResult; D: DD; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void; tab: string | null; metric: string;
  /** selected slot (rep=<label>): its column highlighted */
  sel?: number | null;
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
      { id: "gap", label: "Gap at leg end", get: (c) => c.delta_moving_s, fmt: (v) => gapTxt(v, secTxt), best: "min" },
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
        <div class="head"><h2 title={`${r.legs_kind === "route_waypoints" ? "between the route's common stops" : r.legs_kind === "laps_400m" ? "400 m laps (own distance)" : "per km (own distance)"}; Δ leg = leg time − reference leg time; gap = ghost gap at the leg end (moving time, behind / ahead); faded = partly covered`}>Legs</h2></div>
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
      pace ? { id: "pace", label: "Pace", get: (c) => c.pace_s_km, fmt: (v) => dur(v), best: "min", vsRef: true, dfmt: (d) => sgn(d) }
        : { id: "pace", label: "km/h", get: (c) => c.speed_kmh, fmt: (v) => v.toFixed(1), best: "max", vsRef: true, dfmt: (d) => sgn(d, (x) => x.toFixed(1)) },
      { id: "time", label: "Time", get: (c) => c.time_s, fmt: (v) => dur(v), best: "min", vsRef: true, dfmt: (d) => sgn(d, (x) => x.toFixed(Math.abs(x) < 10 ? 1 : 0)) },
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
      { id: "drop30", label: "HR drop 30 s", get: (c) => c.drop_30, fmt: (v) => String(v), best: "max", vsRef: true },
      { id: "drop60", label: "HR drop 60 s", get: (c) => c.drop_60, fmt: (v) => String(v), best: "max", vsRef: true },
      { id: "peak", label: "HR peak", get: (c) => c.hr_peak, fmt: (v) => String(v), best: null },
      { id: "min", label: "HR min", get: (c) => c.hr_min, fmt: (v) => String(v), best: null },
      { id: "next", label: "HR at next start", get: (c) => c.hr_next_start, fmt: (v) => String(v), best: "min", vsRef: true },
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
        cmp={repCmp} refId={D.refId} sel={sel} />
    );
    const brks = r.breaks && (
      <Matrix<Bk> units={brkUnits} rows={ids.map((id) => ({ id, label: label(id), cells: r.breaks![id].slice(0, -1) }))} metrics={brkMetrics}
        metric={query.get("bm") ?? "dur"} setMetric={mq("bm")} focus={focus} onHover={onHover} onPick={onPick} refRow={brkRef} colors={D.colors}
        cmp={brkCmp} refId={D.refId} sel={sel} />
    );
    const first = r.mode === "intervals" ? "reps" : "breaks";
    const t = tab ?? first;
    return (
      <section>
        <div class="head">
          <div class="seg" title={t === "reps" ? `Columns = the ${D.refId ? "reference's" : "anchor session's"} reps; other sessions' reps are matched to them ${r.rep_summary && Object.values(r.rep_summary).some((x) => x.match === "index") ? "by number" : "by length"} (a rep only some have gets an “R1+” column); Δ time = time at the reference rep's distance − reference time`
            : "After R3 = from the end of rep 3 to the start of the next rep; HR drop = peak around the rep end − HR at 30 / 60 s; the recovery after the last rep is in the Breaks chart (Recovery)"}>
            <button class={t === "reps" ? "on" : ""} onClick={() => setQuery({ tab: first === "reps" ? null : "reps" })}>Reps</button>
            <button class={t === "breaks" ? "on" : ""} onClick={() => setQuery({ tab: first === "breaks" ? null : "breaks" })}>Breaks</button>
          </div>
        </div>
        {t === "reps" && <RepLadder r={r} D={D} ids={ids} pace={pace} focus={focus} onHover={onHover} onPick={onPick} sel={sel} />}
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
function RepLadder({ r, D, ids, pace, focus, onHover, onPick, sel = null }: {
  r: CmpResult; D: DD; ids: string[]; pace: boolean; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void;
  sel?: number | null;
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
  const { byTime, lines, med, refV, fmt, dvs } = RD;
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
  const colorOf = (id: string) => (id === focus && D.many && id !== D.refId ? D.fc : D.colors[id] ?? "#888");
  const order = [...lines.filter((l) => l.id !== D.refId && l.id !== focus), ...lines.filter((l) => l.id === D.refId && l.id !== focus), ...lines.filter((l) => l.id === focus)];
  const path = (v: N[]) => {
    let d = "", pen = false;
    // connect over missing slots (a session without the extra "R1+" rep, an unmatched rep): the dots show which reps exist
    v.forEach((p, k) => { if (p === null) return; d += `${pen ? "L" : "M"}${x(k).toFixed(1)},${y(p).toFixed(1)}`; pen = true; });
    return d;
  };
  return (
    <div class="ladder">
      <div class="ladder-chart" ref={box} title="faster is up; dots = one rep; hover a dot for its value">
        <div class="title">{byTime ? "Time per rep (no distance)" : pace ? "Pace per rep (min/km)" : "Speed per rep (km/h)"} </div>
        <svg width={W} height={H} onMouseLeave={() => onHover(null)}>
          {ticks.map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="grid" /><text x={L - 6} y={y(v) + 4} class="ax" text-anchor="end">{fmt(v)}</text></g>)}
          {sel !== null && slots[sel] && <rect x={x(sel) - 14} y={T} width={28} height={H - T - Bm} class="selcol" />}
          {slots.map((s, k) => <text key={k} x={x(k)} y={H - 6} class={"ax slotlink" + (k === sel ? " on" : "")} text-anchor="middle"
            onClick={() => setQuery({ rep: k === sel ? null : repToUrl(s.label), view: null })}><title>{k === sel ? "Back to all reps" : `Show only ${slotName(s)}`}</title>{slotName(s, true)}</text>)}
          {med && <path d={path(med)} class="med" />}
          {order.map((l) => {
            const on = l.id === focus, isRef = l.id === D.refId;
            const c = colorOf(l.id), wdt = on ? 3 : isRef ? 2.4 : D.many ? 1.2 : 1.6;
            const a = r.activities[l.id];
            return (
              <g key={l.id} onMouseEnter={() => onHover(l.id)} onClick={() => onPick(l.id)} style={{ cursor: "pointer" }}>
                <path d={path(l.v)} fill="none" stroke="transparent" stroke-width={10} />
                {on && <path d={path(l.v)} fill="none" style={{ stroke: "var(--surface)" }} stroke-width={wdt + 3} />}
                <path d={path(l.v)} fill="none" stroke={c} stroke-width={wdt} stroke-opacity={D.many && !on && !isRef ? 0.8 : 1} />
                {l.v.map((p, k) => p === null ? null : (
                  <circle key={k} cx={x(k)} cy={y(p)} r={on || isRef ? 4 : 3} fill={c} style={{ stroke: "var(--surface)" }} stroke-width={1}>
                    <title>{`${a?.date ?? l.id} · ${slotName(slots[k])}: ${fmt(p)}${byTime ? "" : pace ? " /km" : " km/h"}${refV && refV[k] !== null && refV[k] !== undefined && !isRef ? ` (${dvs(p - refV[k]!, D.refId ? "reference" : "median")})` : ""}`}</title>
                  </circle>))}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

/** per-rep values of every session in the active metric + ranking by the mean difference to the reference.
 *  `only` = one slot (the selected rep): values and ranking of that rep alone. The session's own average
 *  (`mean`) is distance-weighted: total rep distance / total rep time (as "avg rep pace" everywhere). */
function repData(r: CmpResult, D: DD, ids: string[], pace: boolean, only: number | null = null) {
  // sessions without distance: rep times instead (shorter = up, like pace)
  const byTime = !ids.some((id) => (r.reps?.[id] ?? []).some((c) => c?.pace_s_km));
  if (byTime) pace = true;
  const val = (c: any): N => (!c ? null : byTime ? c.time_s ?? null : pace ? c.pace_s_km ?? null : c.speed_kmh ?? null);
  const keep = <T,>(a: (T | null)[]) => (only === null ? a : a.map((c, k) => (k === only ? c : null)));
  const lines = ids.map((id) => ({ id, v: keep(r.reps?.[id] ?? []).map(val) }));
  const med = !D.refId && r.reps_ref ? keep(r.reps_ref as any[]).map(val) : null;
  // ranking: mean difference to the reference over the reps both have (s/km or km/h); the reference itself = 0
  const refV = D.refId ? lines.find((l) => l.id === D.refId)?.v ?? null : med;
  const avg = (id: string): N => {
    const cs = keep(r.reps?.[id] ?? []).filter((c): c is NonNullable<typeof c> => !!c);
    let t = 0, d = 0, n = 0;
    for (const c of cs) { if (c.time_s) { t += c.time_s; n++; } if (c.time_s && c.dist_m) d += c.dist_m; }
    if (!n) return null;
    if (byTime) return t / n;
    if (!d) return null;
    return pace ? (1000 * t) / d : (3.6 * d) / t;
  };
  const rank = lines.map((l) => {
    const d: number[] = [];
    if (refV) l.v.forEach((p, k) => { if (p !== null && refV[k] !== null && refV[k] !== undefined) d.push(p - refV[k]!); });
    return { id: l.id, n: d.length, d: d.length ? d.reduce((a, b) => a + b, 0) / d.length : null, mean: avg(l.id) };
  }).sort((a, b) => (a.d === null ? 1 : b.d === null ? -1 : pace ? a.d - b.d : b.d - a.d));
  const fmt = (v: number) => (pace ? dur(v) : v.toFixed(1));
  // a difference in words: "3 s/km faster", "0.4 km/h slower", "2.1 s faster" (time)
  const dtxt = (d: number) => {
    const fast = pace ? d < 0 : d > 0, a = Math.abs(d);
    const t = byTime ? secTxt(a) : pace ? `${Math.round(a)} s/km` : `${a.toFixed(1)} km/h`;
    return !/[1-9]/.test(t) ? "same" : `${t} ${fast ? "faster" : "slower"}`;
  };
  /** "3 s/km faster than the reference" / "same as the median" */
  const dvs = (d: number, who: string) => { const t = dtxt(d); return t === "same" ? `same as the ${who}` : `${t} than the ${who}`; };
  const faster = (d: number) => (pace ? d < 0 : d > 0);
  return { byTime, pace, lines, med, refV, rank, fmt, dtxt, dvs, faster };
}

function RepRank({ r, D, RD, focus, onHover, onPick, compact, slot = null }: {
  r: CmpResult; D: DD; RD: ReturnType<typeof repData>; focus: string | null; onHover: (id: string | null) => void; onPick: (id: string) => void; compact?: boolean;
  /** "Rep 3": the ranking is of that rep only */
  slot?: string | null;
}) {
  const { rank, fmt, dtxt, dvs, faster, byTime, pace } = RD;
  const maxD = Math.max(1e-9, ...rank.map((q) => Math.abs(q.d ?? 0)));
  const colorOf = (id: string) => (id === focus && D.many && id !== D.refId ? D.fc : D.colors[id] ?? "#888");
  const what = byTime ? "rep time" : pace ? "rep pace" : "rep speed";
  const vs = D.refId ? "reference" : "median";
  return (
    <div class="ladder-rank" title={`${slot ? `${slot} ${what}` : `avg ${what} = rep distance / rep time`}; bar right = faster than the ${vs}, left = slower${compact ? "; hover the charts for values at a point" : ""}`}>
      <div class="title">{slot ? <><b>{slot}</b>: who was faster, vs the {vs}</> : compact ? `Who was faster: avg ${what} vs the ${vs}` : `Avg vs the ${vs}`}
        {" "}<span class="muted">{slot ? `(${byTime ? "rep time" : what})` : `(difference: mean over the reps both have)`}</span></div>
      <table>
        <tbody onMouseLeave={() => onHover(null)}>
          {rank.map((q) => (
            <tr key={q.id} class={q.id === focus ? "hl" : ""} onMouseEnter={() => onHover(q.id)} onClick={() => onPick(q.id)}>
              <td><i class="sw" style={{ background: colorOf(q.id) }} />{r.activities[q.id]?.date ?? q.id}{q.id === D.refId ? <span class="muted"> ref</span> : ""}</td>
              <td class="num">{q.mean === null ? "—" : fmt(q.mean)}</td>
              <td class="num dl" title={q.d === null ? "" : slot ? `${dvs(q.d, vs)} in ${slot}` : `${dvs(q.d, vs)}: mean over ${q.n} rep${q.n === 1 ? "" : "s"} both have`}>{q.id === D.refId ? "" : q.d === null ? "—" : dtxt(q.d)}</td>
              <td class="bar">{q.d !== null && q.id !== D.refId && <span class={faster(q.d) ? "f" : "s"} style={{ width: `${(Math.abs(q.d) / maxD) * 50}%` }} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- one rep / per rep
/** chart data for one slot of rep_series: x = seconds since each session's rep start (shared 1 s grid, real time);
 *  repSpeed = the rep itself only (0 .. own rep time) for the in-rep gap; t = file elapsed time (map markers) */
function repViewD(r: CmpResult, D: DD, k: number, rawPace = false): DD {
  const S = r.rep_series!.slots[k];
  const X = Array.from({ length: S.n }, (_, i) => S.x0 + i);
  const sids = D.sids.filter((i) => S.series[i]);
  const speed: Record<string, N[]> = {}, raw: Record<string, N[]> = {}, repSpeed: Record<string, N[]> = {}, hr: Record<string, N[]> = {}, t: Record<string, N[]> = {};
  for (const id of sids) {
    const q = S.series[id];
    raw[id] = q.speed_kmh;
    speed[id] = rawPace ? q.speed_kmh : smoothRep(q.speed_kmh);
    repSpeed[id] = q.speed_kmh.map((v, i) => (X[i] >= 0 && X[i] <= q.time_s ? v : null));
    hr[id] = q.hr;
    t[id] = X.map((x) => q.start_s + x);
  }
  const refId = D.refId && S.series[D.refId] ? D.refId : null;
  return { ...D, sids, refId, order: D.order.filter((i) => S.series[i]), X, speed, raw, repSpeed, hr, alt: {}, delta: {}, t, mov: {}, routeAlt: null };
}

/** the part of the chart data with lo <= x <= hi (Breaks: one break) */
function sliceD(D: DD, lo: number, hi: number): DD {
  let a = D.X.findIndex((x) => x >= lo), b = D.X.length - 1;
  while (b > 0 && D.X[b] > hi) b--;
  if (a < 0 || b <= a) return D;
  const cut = (m: Record<string, N[]>) => Object.fromEntries(Object.entries(m).map(([k, v]) => [k, v.slice(a, b + 1)]));
  return { ...D, X: D.X.slice(a, b + 1), speed: cut(D.speed), raw: cut(D.raw), repSpeed: cut(D.repSpeed), hr: cut(D.hr), alt: cut(D.alt),
    delta: cut(D.delta), t: cut(D.t), mov: cut(D.mov), routeAlt: D.routeAlt ? D.routeAlt.slice(a, b + 1) : null };
}

/** Intervals: Show All / Per rep / R1 … Rn; Breaks: All / After R1 … / Recovery (rep=R<slot label>, view=reps).
 *  Buttons on wide screens (up to 10 reps), a select on narrow screens and for long sets. */
function RepPick({ r, mode, sel, perRep }: { r: CmpResult; mode: Mode; sel: string | null; perRep: boolean }) {
  const all = r.slots ?? [];
  const slots = mode === "breaks" ? all.map((s, k) => ({ s, k })).filter(({ k }) => (r.axis?.break_width?.[k] ?? 0) > 0) : all.map((s, k) => ({ s, k }));
  if (!slots.length) return null;
  const brk = mode === "breaks";
  const name = (s: { label: string; anchor: boolean }, k: number) => (brk ? breakName(all, k, true) : slotName(s, true));
  const tip = (s: { label: string; anchor: boolean }, k: number) => (brk ? (k === all.length - 1 ? `Only the recovery after the last rep (${slotName(s)}), up to 2 min` : `Only the break after ${slotName(s)}`)
    : `Only ${slotName(s)}: every session from 0 at that rep's start, in real time`);
  const go = (label: string | null) => setQuery({ rep: label ? repToUrl(label) : null, view: null });
  const val = perRep ? "_per" : sel ?? "";
  return <span class="grp ctx">
    <span class="muted">{brk ? "Break" : "Show"}</span>
    <div class={"seg reppick" + (slots.length > 10 ? " many" : "")}>
      <button class={!sel && !perRep ? "on" : ""} onClick={() => go(null)} title={brk ? "All breaks" : "All reps on one timeline"}>All</button>
      {!brk && <button class={perRep ? "on" : ""} onClick={() => setQuery({ view: "reps", rep: null })} title="One small chart per rep, every session overlaid, each from 0 at the rep start">Per rep</button>}
      {slots.length <= 10 && slots.map(({ s, k }) => <button key={s.label} class={"rb" + (sel === s.label ? " on" : "")} title={tip(s, k)}
        onClick={() => go(sel === s.label ? null : s.label)}>{name(s, k)}</button>)}
    </div>
    <select class={"reppick-sel" + (slots.length > 10 ? " many" : "")} value={val} aria-label={brk ? "Break" : "Rep"}
      onChange={(e) => { const v = (e.target as HTMLSelectElement).value; v === "_per" ? setQuery({ view: "reps", rep: null }) : go(v || null); }}>
      <option value="">{brk ? "All breaks" : "All reps"}</option>
      {!brk && <option value="_per">Per rep</option>}
      {slots.map(({ s, k }) => <option key={s.label} value={s.label}>{brk ? breakName(all, k) : slotName(s)}</option>)}
    </select>
  </span>;
}

// ---------------------------------------------------------------- Options (expert controls) + chips for the changed ones
interface Opts {
  mode: Mode; iv: boolean; perRep: boolean; repOne: boolean; refKind: "first" | "median"; startQ: string | null; rep1: boolean; hasReps: boolean;
  metric: "pace" | "speed"; defMetric: "pace" | "speed"; deltaKind: string; repAxis: string; gap: string; rgap: string; match: string;
  rawRep: boolean; startName: string | null;
}
const ROUTE_MODES = "Route position, Moving time and Elapsed";

function OptionsBar({ o }: { o: Opts }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const allView = o.mode === "intervals" && !o.perRep && !o.repOne;
  const repCharts = o.mode === "intervals";
  const startTxt = o.rep1 ? "Rep 1" : o.startQ ? (o.startName ? `common, ${o.startName}` : "common") : "own";
  // changed options that apply to this view: one chip each, × resets it
  const chips: { k: string; label: string; title: string; reset: Record<string, null> }[] = [];
  if (o.refKind === "median") chips.push({ k: "ref", label: "Reference: median", title: "Gaps and deltas vs the median of the sessions", reset: { ref: null } });
  if (!o.iv && o.startQ) chips.push({ k: "start", label: `Start: ${startTxt}`, title: o.rep1 ? "t = 0 at each session's rep 1" : "Common start line", reset: { start: null, srad: null } });
  if (o.mode !== "breaks" && o.metric !== o.defMetric) chips.push({ k: "metric", label: o.metric === "pace" ? "Pace" : "Speed", title: "Pace / speed", reset: { metric: null } });
  if (o.mode === "distance" && o.deltaKind === "elapsed") chips.push({ k: "delta", label: "Gap in elapsed time", title: "Stops count", reset: { delta: null } });
  if (allView && o.repAxis === "normalized") chips.push({ k: "axis", label: "Rep axis: % of rep", title: "x within a rep = 0–100 %", reset: { rep_axis: null } });
  if (allView && o.gap !== "60") chips.push({ k: "gap", label: `Breaks squeezed to ${o.gap} s`, title: "Break width on the rep timeline", reset: { gap: null } });
  if (o.mode === "breaks" && o.rgap !== "30") chips.push({ k: "rgap", label: `Reps squeezed to ${o.rgap} s`, title: "Rep width on the break timeline", reset: { rgap: null } });
  if ((o.iv || o.rep1) && o.match === "index") chips.push({ k: "match", label: "Reps matched by number", title: "Rep 1 vs rep 1, …", reset: { match: null } });
  if (repCharts && o.rawRep) chips.push({ k: "raw", label: "Raw 1 s pace", title: "Intervals: pace / speed lines without smoothing", reset: { smooth: null } });
  const Row = ({ label, on, why, children }: { label: string; on: boolean; why?: string; children: any }) => (
    <div class={"orow" + (on ? "" : " off")} title={on ? undefined : why}>
      <span class="olab">{label}</span>
      <fieldset disabled={!on}>{children}</fieldset>
      {!on && why && <span class="owhy">{why}</span>}
    </div>
  );
  const Seg = ({ items }: { items: { on: boolean; label: string; title?: string; q: Record<string, string | null>; show?: boolean }[] }) => (
    <div class="seg">{items.filter((x) => x.show !== false).map((x) => <button key={x.label} class={x.on ? "on" : ""} title={x.title} onClick={() => setQuery(x.q)}>{x.label}</button>)}</div>
  );
  const sel = (k: string, v: string, def: string, vals: string[]) => (
    <select value={v} onChange={(e) => { const x = (e.target as HTMLSelectElement).value; setQuery({ [k]: x === def ? null : x }); }}>
      {vals.map((x) => <option key={x} value={x}>{x} s</option>)}
    </select>
  );
  return (
    <div class="optbar" ref={box}>
      {chips.map((c) => <span key={c.k} class="ochip" title={c.title}>{c.label}
        <button class="x" title="Back to the default" aria-label={`Reset ${c.label}`} onClick={() => setQuery(c.reset)}>×</button></span>)}
      <button class={"optbtn" + (open ? " on" : "")} aria-expanded={open} onClick={() => setOpen(!open)}
        title="Reference, start, pace / speed, gap time, rep axis, break width, rep matching">Options{chips.length ? ` (${chips.length})` : ""}</button>
      {open && (
        <div class="optpop" role="dialog">
          <Row label="Reference" on>
            <Seg items={[
              { on: o.refKind === "first", label: "First session", title: "The first session is the reference (ref on its chip; ref on another chip makes that one first)", q: { ref: null } },
              { on: o.refKind === "median", label: "Median", title: "Median of all selected sessions", q: { ref: "median" } },
            ]} />
          </Row>
          <Row label="Start" on={!o.iv} why={`${ROUTE_MODES} only`}>
            <Seg items={[
              { on: !o.startQ, label: "Own", title: "Every session from its own watch start", q: { start: null } },
              { on: !!o.startQ && !o.rep1, label: "Common", q: { start: !o.startQ || o.rep1 ? "ref" : o.startQ },
                title: "Common start line at the first session's start (“start” on a chip: use that session's start). Lead-ins are cut off, sessions that started later join where their recording begins on the start session's path, sessions that never get on it are left out" },
              { on: o.rep1, label: "Rep 1", show: o.hasReps || o.rep1, q: { start: "rep1" },
                title: "t = 0 at rep 1: every session starts at the detected start of its first rep (the rep matched to the reference's rep 1, as in Intervals); the warm-up is cut off, real breaks are kept. Sessions without reps are left out. Route position: x = own distance since rep 1" },
            ]} />
          </Row>
          <Row label="Show" on={o.mode !== "breaks"} why="Breaks: always speed (pace is meaningless while standing)">
            <Seg items={[
              { on: o.metric === "pace", label: "Pace", title: "min/km", q: { metric: o.defMetric === "pace" ? null : "pace" } },
              { on: o.metric === "speed", label: "Speed", title: "km/h", q: { metric: o.defMetric === "speed" ? null : "speed" } },
            ]} />
          </Row>
          <Row label="Gap in" on={o.mode === "distance"} why="Route position only">
            <Seg items={[
              { on: o.deltaKind === "moving", label: "Moving time", title: "Stops don't count", q: { delta: null } },
              { on: o.deltaKind === "elapsed", label: "Elapsed", title: "Stops count", q: { delta: "elapsed" } },
            ]} />
          </Row>
          <Row label="Rep axis" on={allView} why="Intervals › All only">
            <Seg items={[
              { on: o.repAxis !== "normalized", label: "Seconds", title: "Seconds since the rep start", q: { rep_axis: null } },
              { on: o.repAxis === "normalized", label: "% of rep", title: "0–100 % of each rep", q: { rep_axis: "normalized" } },
            ]} />
          </Row>
          <Row label="Break width" on={allView} why="Intervals › All only">{sel("gap", o.gap, "60", ["15", "30", "60", "90", "120"])}</Row>
          <Row label="Rep width" on={o.mode === "breaks"} why="Breaks only">{sel("rgap", o.rgap, "30", ["10", "20", "30", "60"])}</Row>
          <Row label="Match reps" on={o.iv || o.rep1} why="Intervals, Breaks and Start = Rep 1 only">
            <Seg items={[
              { on: o.match !== "index", label: "By length", title: "Pair reps by distance / duration (order kept)", q: { match: null } },
              { on: o.match === "index", label: "By number", title: "Rep 1 vs rep 1, …", q: { match: "index" } },
            ]} />
          </Row>
          <Row label="Pace line" on={repCharts} why="Intervals only">
            <Seg items={[
              { on: !o.rawRep, label: "Smoothed", title: "7 s rolling median + 7 s mean: GPS jitter and spikes removed", q: { smooth: null } },
              { on: o.rawRep, label: "Raw 1 s", title: "The 1 s values as recorded", q: { smooth: "raw" } },
            ]} />
          </Row>
        </div>
      )}
    </div>
  );
}

/** one small chart per rep slot (rep_series): every session overlaid from 0 at its rep start, the same pace / speed
 *  scale in all; click = open that rep (rep=<label>) */
function PerRep({ r, D, metric, focus, onHover, onCursor, raw }: {
  r: CmpResult; D: DD; metric: "pace" | "speed"; focus: string | null; onHover: (id: string | null) => void; raw: boolean;
  onCursor: (c: { k: number; i: number } | null) => void;
}) {
  const pace = metric === "pace";
  const charts = useMemo(() => {
    if (!r.rep_series || !r.slots) return [];
    const vs = r.rep_series.slots.map((_s, k) => repViewD(r, D, k, raw));
    const conv = (a: N[]) => (pace ? a.map(kmhToPace) : a);
    // shared y range: p3–p97 of the reps themselves (from 8 s in, each session's own rep time) of every slot,
    // padded, so the reps fill the chart and only stray spikes leave it
    const all: N[] = [];
    vs.forEach((V, k) => { const S = r.rep_series!.slots[k]; for (const id of V.sids) { const tt = S.series[id]?.time_s ?? S.width; conv(V.speed[id]).forEach((v, i) => { if (V.X[i] >= 8 && V.X[i] <= tt) all.push(v); }); } });
    const yr = pRange(all, raw ? 0.02 : 0.01, raw ? 0.98 : 0.99, 0.15) ?? (pace ? paceRange(all, { moveFrac: 0.5, hi: 0.98 }) : speedRange(all, { moveFrac: 0.5 })) ?? null;
    return vs.map((V, k) => {
      const ys: Record<string, N[]> = {};
      for (const id of V.sids) ys[id] = conv(V.speed[id]);
      const P: CPanel = { key: "speed", label: "", ys, fmt: pace ? (v) => dur(v) : (v) => v.toFixed(1), height: 120, dir: pace ? -1 : 1, yRange: yr,
        axis: pace ? "pace" : "speed" };
      return { V, P: [P], spans: [{ a: 0, b: r.rep_series!.slots[k].width }], slot: r.slots![k] };
    });
  }, [r, D, pace, raw]);
  if (!charts.length) return <div class="empty">Loading the reps…</div>;
  return (
    <div class="perrep">
      {charts.map(({ V, P, spans, slot }, k) => (
        <div key={slot.label} class="perrep-cell" title={`Open ${slotName(slot)}`} onClick={() => setQuery({ rep: repToUrl(slot.label), view: null })}>
          <div class="perrep-h"><b>{slotName(slot)}</b><span class="muted">{V.sids.length} session{V.sids.length === 1 ? "" : "s"}{slot.anchor ? "" : " (extra rep)"}</span><span class="open">open ›</span></div>
          <CompareCharts X={V.X} panels={P} order={V.order} colors={V.colors} refId={V.refId} focus={focus} many={V.many} spans={spans} ticks={null}
            xFmt={sdur} xIncrs={[15, 30, 60, 120, 300, 600]} zoom={false} xSpace={45} onPick={onHover}
            onCursor={(i) => onCursor(i === null ? null : { k, i })} />
        </div>
      ))}
    </div>
  );
}
