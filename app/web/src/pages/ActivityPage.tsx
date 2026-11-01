// One activity: map with the track coloured by speed / HR / elevation, stacked charts with linked hover
// (chart <-> map), pool-swim summary, one "Segments" section (reps / legs & pauses / laps / lengths as tabs).
// Hovering a table row highlights that stretch on the map.
import type { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import { Popup } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useApi, type ActivityDetail, type Lap, type Leg, type Series, type SwimLength } from "../lib/api";
import { currentPerson, setQuery, useLocation } from "../lib/router";
import { dur, num, pace, pace100, kmh, time, longDate, onFoot, repLabel, secs, speedText as speedOf } from "../lib/format";
import { RAMP, quantile } from "../lib/geo";
import { MapView, setData, fitBBox, EMPTY } from "../components/MapView";
import { SeriesCharts, drawablePanels, type Band, type PanelKey } from "../components/SeriesCharts";
import { Table, type Col } from "../components/Table";
import { DistCell, SportDot, placeHref, speedText, typeText, workoutTip, togetherWith } from "../components/ActivityBits";
import { RecordChips, WorkoutProgress, fastest } from "../components/ProgressCard";
import { PageHeader } from "../components/PageHeader";
import { NearestPanel } from "../components/NearestPanel";
import { ShapeSection } from "../components/ShapeSection";

interface Track { t: number[]; lat: number[]; lon: number[]; speed: (number | null)[]; hr: (number | null)[]; alt: (number | null)[] }
type ColorBy = "speed" | "hr" | "alt";

/** index of the largest t <= x (binary search) */
function nearestIdx(ts: number[], x: number): number {
  let lo = 0, hi = ts.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ts[m] <= x) lo = m; else hi = m - 1; }
  if (lo + 1 < ts.length && Math.abs(ts[lo + 1] - x) < Math.abs(ts[lo] - x)) return lo + 1;
  return lo;
}

function colorStops(vals: (number | null)[], by: ColorBy): [number, number] {
  // speed: ignore standing still so the ramp spans the moving range
  const v = vals.filter((x): x is number => x !== null && (by === "speed" ? x > 0.8 : by === "alt" ? true : x > 0));
  const lo = quantile(v, by === "speed" ? 0.1 : 0.05), hi = quantile(v, 0.95);
  return [lo, hi > lo ? hi : lo + 1];
}

function rampExpr(lo: number, hi: number): any {
  const stops: any[] = [];
  RAMP.forEach((c, i) => stops.push(lo + ((hi - lo) * i) / (RAMP.length - 1), c));
  return ["case", ["==", ["get", "v"], null], "#9aa0a6", ["interpolate", ["linear"], ["get", "v"], ...stops]];
}

function ActivityMap({ track, colorBy, sport, pauses, cursor, span, onHover }: {
  track: Track; colorBy: ColorBy; sport: string; pauses: ActivityDetail["pauses"];
  cursor: [number, number] | null; span: [number, number] | null; onHover: (t: number | null) => void;
}) {
  const [map, setMap] = useState<MLMap | null>(null);
  const hov = useRef(onHover);
  hov.current = onHover;
  const trRef = useRef(track);
  trRef.current = track;
  const vals = track[colorBy];
  const [lo, hi] = useMemo(() => colorStops(vals, colorBy), [vals, colorBy]);

  const onReady = (m: MLMap) => {
    for (const s of ["seg", "span", "cursor", "pauses", "ends"]) setData(m, s, EMPTY);
    m.addLayer({ id: "seg-casing", type: "line", source: "seg", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#fff", "line-width": 6.5 } });
    m.addLayer({ id: "seg", type: "line", source: "seg", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#888", "line-width": 4 } });
    m.addLayer({ id: "span", type: "line", source: "span", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#1d2125", "line-width": 7, "line-opacity": 0.55 } });
    m.addLayer({ id: "pauses", type: "circle", source: "pauses", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "dur"], 15, 4, 300, 8], "circle-color": "#fff", "circle-stroke-color": "#67707a", "circle-stroke-width": 2 } });
    m.addLayer({ id: "ends", type: "circle", source: "ends", paint: { "circle-radius": 6, "circle-color": ["get", "c"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    m.addLayer({ id: "cursor", type: "circle", source: "cursor", paint: { "circle-radius": 7, "circle-color": "#1d2125", "circle-stroke-color": "#fff", "circle-stroke-width": 2.5 } });
    const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 10 });
    m.on("mousemove", (e: MapMouseEvent) => {
      const p = e.point;
      const pf = m.queryRenderedFeatures([[p.x - 6, p.y - 6], [p.x + 6, p.y + 6]], { layers: ["pauses"] });
      if (pf.length) {
        const q = pf[0].properties as any;
        popup.setLngLat(e.lngLat).setHTML(`Pause ${dur(q.dur)} at ${q.km.toFixed(2)} km${q.timer ? " (timer stopped)" : ""}`).addTo(m);
      } else popup.remove();
      const fs = m.queryRenderedFeatures([[p.x - 10, p.y - 10], [p.x + 10, p.y + 10]], { layers: ["seg"] });
      if (!fs.length) { hov.current(null); m.getCanvas().style.cursor = ""; return; }
      m.getCanvas().style.cursor = "crosshair";
      // nearest segment start to the mouse
      let best = fs[0], bd = Infinity;
      for (const f of fs) {
        const c = (f.geometry as GeoJSON.LineString).coordinates[0];
        const s = m.project(c as [number, number]);
        const d = (s.x - p.x) ** 2 + (s.y - p.y) ** 2;
        if (d < bd) { bd = d; best = f; }
      }
      hov.current(trRef.current.t[(best.properties as any).i]);
    });
    m.on("mouseout", () => { hov.current(null); popup.remove(); });
    setMap(m);
  };

  useEffect(() => {
    if (!map) return;
    let w = 180, s = 90, e = -180, n = -90;
    for (let i = 0; i < track.t.length; i++) {
      w = Math.min(w, track.lon[i]); e = Math.max(e, track.lon[i]); s = Math.min(s, track.lat[i]); n = Math.max(n, track.lat[i]);
    }
    const L = track.t.length - 1;
    setData(map, "ends", { type: "FeatureCollection", features: [
      { type: "Feature", properties: { c: "#1d2125" }, geometry: { type: "Point", coordinates: [track.lon[L], track.lat[L]] } },
      { type: "Feature", properties: { c: "#2f8a4c" }, geometry: { type: "Point", coordinates: [track.lon[0], track.lat[0]] } },
    ] });
    fitBBox(map, [w, s, e, n], { padding: 40, maxZoom: 17 });
  }, [map, track]);

  useEffect(() => {
    if (!map) return;
    // one 2-point feature per sample so each can carry its own colour value + index for hover
    const f: GeoJSON.Feature[] = [];
    for (let i = 0; i + 1 < track.t.length; i++) {
      if (track.t[i + 1] - track.t[i] > 120) continue;
      f.push({ type: "Feature", properties: { i, v: vals[i] }, geometry: { type: "LineString", coordinates: [[track.lon[i], track.lat[i]], [track.lon[i + 1], track.lat[i + 1]]] } });
    }
    setData(map, "seg", { type: "FeatureCollection", features: f });
    map.setPaintProperty("seg", "line-color", rampExpr(lo, hi));
  }, [map, colorBy, track, lo, hi]);

  useEffect(() => {
    if (!map) return;
    setData(map, "pauses", { type: "FeatureCollection", features: pauses.filter((p) => p.lat !== null).map((p) => ({
      type: "Feature", properties: { dur: p.dur_s, km: p.km, timer: p.timer }, geometry: { type: "Point", coordinates: [p.lon!, p.lat!] } })) });
  }, [map, pauses]);

  useEffect(() => {
    if (!map) return;
    setData(map, "cursor", cursor ? { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: cursor } } : EMPTY);
  }, [map, cursor]);

  useEffect(() => {
    if (!map) return;
    if (!span) { setData(map, "span", EMPTY); return; }
    const c: number[][] = [];
    for (let i = 0; i < track.t.length; i++) if (track.t[i] >= span[0] && track.t[i] <= span[1]) c.push([track.lon[i], track.lat[i]]);
    setData(map, "span", c.length > 1 ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: c } } : EMPTY);
  }, [map, span, track]);

  const fmt = (v: number) => colorBy === "speed" ? speedOf(sport, v) : colorBy === "hr" ? `${Math.round(v)} bpm` : `${Math.round(v)} m`;
  return (
    <MapView onReady={onReady} class="bordered">
      <div class="overlay bl ramp">
        <span>{fmt(lo)}</span><i style={{ background: `linear-gradient(90deg, ${RAMP.join(",")})` }} /><span>{fmt(hi)}</span>
      </div>
    </MapView>
  );
}


const STROKE: Record<string, string> = { 0: "free", 1: "back", 2: "breast", 3: "fly", 4: "drill", 5: "mixed", 6: "IM" };
const speedCell = (sport: string, ms: number | null | undefined) => (onFoot(sport) ? pace(ms) : sport === "swim" ? pace100(ms) : kmh(ms));
const speedHead = (sport: string) => (onFoot(sport) ? "Pace /km" : sport === "swim" ? "/100 m" : "km/h");
/** drop columns whose cells are all empty in these rows (e.g. HR on a swim, intensity on old files) */
const nonEmpty = <T,>(cols: Col<T>[], rows: T[], keys: Record<string, (r: T) => unknown>) =>
  cols.filter((c) => !keys[c.key] || rows.some((r) => { const v = keys[c.key](r); return v !== null && v !== undefined && v !== ""; }));

function lapCols(sport: string, laps: Lap[]): Col<Lap>[] {
  const swim = laps.some((l) => l.kind);
  const triggers = new Set(laps.map((l) => l.trigger));
  const cols: Col<Lap>[] = [
    { key: "n", label: "Lap", num: true, render: (r) => laps.indexOf(r) + 1, sort: (r) => r.start },
    ...(swim ? [{ key: "kind", label: "Kind", render: (r: Lap) => r.kind ?? "—" }] : []),
    { key: "start", label: "Start", num: true, render: (r) => dur(r.start) },
    { key: "time", label: "Time", num: true, render: (r) => dur(r.timer ?? r.time) },
    ...(swim ? [{ key: "swim_s", label: "Swimming", num: true, render: (r: Lap) => dur(r.swim_s) }] : []),
    { key: "dist", label: "Dist (m)", num: true, render: (r) => num(r.dist) },
    swim ? { key: "pace_100m_s", label: "/100 m", num: true, render: (r: Lap) => dur(r.pace_100m_s) }
      : { key: "speed", label: speedHead(sport), num: true, render: (r) => speedCell(sport, r.speed) },
    { key: "hr", label: "Avg HR", num: true, render: (r) => num(r.hr) },
    { key: "maxhr", label: "Max HR", num: true, render: (r) => num(r.maxhr) },
    { key: "climb", label: "Climb", num: true, render: (r) => num(r.climb) },
    { key: "intensity", label: "Intensity", render: (r) => r.intensity ?? "—" },
    ...(triggers.size > 1 ? [{ key: "trigger", label: "Trigger", render: (r: Lap) => r.trigger ?? "—" }] : []),
  ];
  return nonEmpty(cols, laps, { hr: (r) => r.hr, maxhr: (r) => r.maxhr, climb: (r) => r.climb, intensity: (r) => r.intensity });
}

/** a leg as shown: legs shorter than 100 m (GPS jitter between two stops) are merged into the next leg (the
 *  last one into the previous); `a`/`b` = time span on the track (incl. the short stop in between) */
interface LegRow extends Leg { a: number; b: number; merged: number }
function mergeLegs(legs: Leg[], min = 100): LegRow[] {
  const out: LegRow[] = [];
  let acc: LegRow | null = null;
  const add = (x: LegRow, l: Leg): LegRow => {
    const d = x.dur_s + l.dur_s, hr = x.hr !== null && l.hr !== null ? (x.hr * x.dur_s + l.hr * l.dur_s) / d : x.hr ?? l.hr;
    const dist = x.dist_m + l.dist_m, climb = x.climb_m + l.climb_m;
    return { ...x, dur_s: d, dist_m: dist, kmh: d ? (dist / d) * 3.6 : 0, hr, hr_max: Math.max(x.hr_max ?? 0, l.hr_max ?? 0) || null,
      climb_m: climb, desc_m: x.desc_m + l.desc_m, grade_pct: dist > 50 ? ((climb - x.desc_m - l.desc_m) / dist) * 100 : null,
      b: l.t + l.dur_s, merged: x.merged + 1 };
  };
  for (const l of legs) {
    const row: LegRow = { ...l, a: l.t, b: l.t + l.dur_s, merged: 0 };
    acc = acc ? add(acc, l) : row;
    if (acc.dist_m >= min) { out.push(acc); acc = null; }
  }
  if (acc) { if (out.length) out[out.length - 1] = add(out[out.length - 1], acc); else out.push(acc); }
  return out;
}

type SegTab = "reps" | "legs" | "laps" | "lengths";

function Segments({ data, sport, setSpan, setCursor }: { data: ActivityDetail; sport: string;
  setSpan: (s: [number, number] | null) => void; setCursor: (c: [number, number] | null) => void }) {
  const { query } = useLocation();
  const reps = data.reps.filter((r) => r.start !== undefined);
  const legs = useMemo(() => mergeLegs(data.legs), [data]);
  const lengths: SwimLength[] = data.swim?.lengths ?? [];
  const tabs: [SegTab, string, number][] = [];
  if (reps.length) tabs.push(["reps", "Reps", reps.length]);
  if (lengths.length) tabs.push(["lengths", "Lengths", lengths.length]);
  if (legs.length || data.pauses.length) tabs.push(["legs", "Legs & pauses", legs.length]);
  if (data.laps.length > 1) tabs.push(["laps", "Watch laps", data.laps.length]);
  if (!tabs.length) return null;
  const want = query.get("seg") as SegTab | null;
  const tab = tabs.find((t) => t[0] === want)?.[0] ?? tabs[0][0];
  const nMerged = legs.reduce((s, l) => s + l.merged, 0);
  const sub: Record<SegTab, string> = {
    reps: "hard stretches found in the pace and HR data; Break = start of this rep to start of the next minus the rep (walk / jog / stand)",
    lengths: "every pool length from the watch; rests are the gaps between lengths",
    legs: `moving stretches between stops ≥ 15 s${nMerged ? `; ${nMerged} legs under 100 m merged into the next` : ""}. Stops shorter than a rep break are pauses too, so pauses ≠ breaks`,
    laps: "as recorded by the watch (lap button, auto-lap or the workout's steps)",
  };
  const sw = (r: { strokes: number | null; time: number }) => (r.strokes && r.time ? (r.strokes / r.time) * 60 : null);
  return (
    <section>
      <div class="head">
        <h2>Segments</h2>
        {tabs.length > 1 ? (
          <div class="seg">
            {tabs.map(([k, label, n]) => (
              <button key={k} class={tab === k ? "on" : ""} onClick={() => setQuery({ seg: k === tabs[0][0] ? null : k })}>{label} <span class="muted">{n}</span></button>
            ))}
          </div>
        ) : <span class="count">{tabs[0][1]} · {tabs[0][2]}</span>}
        <span class="sub">{sub[tab]}{data.activity.track && tab !== "lengths" ? "; hover a row to see it on the map" : ""}</span>
      </div>
      {tab === "reps" && (
        <Table rows={reps} rowKey={(_r, i) => String(i)} onHover={(r) => setSpan(r ? [r.start!, r.end!] : null)} cols={nonEmpty([
          { key: "n", label: "Rep", render: (r) => repLabel(reps.indexOf(r) + 1), sort: (r) => r.start },
          { key: "start", label: "Start", num: true, render: (r) => dur(r.start) },
          { key: "time", label: "Time", num: true, render: (r) => secs(r.time) },
          { key: "dist", label: "Dist (m)", num: true, render: (r) => num(r.dist) },
          { key: "speed", label: speedHead(sport), num: true, render: (r) => speedCell(sport, r.speed) },
          { key: "hr", label: "Avg HR", num: true, render: (r) => num(r.hr) },
          { key: "maxhr", label: "Max HR", num: true, render: (r) => num(r.maxhr) },
          { key: "brk", label: "Break after", num: true, render: (r) => { const b = data.breaks[reps.indexOf(r)]?.time; return b === undefined || b === null ? "" : secs(b); }, sort: (r) => data.breaks[reps.indexOf(r)]?.time ?? null },
        ], reps, { hr: (r) => r.hr, maxhr: (r) => r.maxhr })} />
      )}
      {tab === "lengths" && (
        <Table rows={lengths} rowKey={(_r, i) => String(i)} maxHeight="420px" cols={[
          { key: "n", label: "Length", num: true, render: (r) => lengths.indexOf(r) + 1, sort: (r) => r.start },
          { key: "start", label: "Start", num: true, render: (r) => dur(r.start) },
          { key: "time", label: "Time", num: true, render: (r) => dur(r.time) },
          { key: "pace", label: "/100 m", num: true, render: (r) => (data.swim?.pool_m ? dur((r.time / data.swim.pool_m) * 100) : "—"), sort: (r) => r.time },
          { key: "rest", label: "Rest after", num: true, render: (r) => { const nx = lengths[lengths.indexOf(r) + 1]; const g = nx ? nx.start - r.start - r.time : null; return g !== null && g >= 5 ? dur(g) : ""; },
            sort: (r) => { const i = lengths.indexOf(r), nx = lengths[i + 1]; return nx ? nx.start - r.start - r.time : null; } },
          { key: "strokes", label: "Strokes", num: true, render: (r) => num(r.strokes) },
          { key: "rate", label: "Strokes/min", num: true, render: (r) => num(sw(r)), sort: sw },
          { key: "stroke", label: "Stroke", render: (r) => (r.stroke === null ? "—" : STROKE[String(r.stroke)] ?? String(r.stroke)) },
        ]} />
      )}
      {tab === "legs" && (
        <div class="cols" style={{ gridTemplateColumns: "3fr 2fr" }}>
          <Table rows={legs} rowKey={(_r, i) => String(i)} maxHeight="420px" empty="No legs." onHover={(r) => setSpan(r ? [r.a, r.b] : null)} cols={nonEmpty<LegRow>([
            { key: "n", label: "Leg", num: true, render: (r) => legs.indexOf(r) + 1, sort: (r) => r.t },
            { key: "t", label: "Start", num: true, render: (r) => dur(r.t) },
            { key: "dur_s", label: "Moving", num: true, render: (r) => dur(r.dur_s) },
            { key: "dist_m", label: "km", num: true, render: (r) => (r.dist_m / 1000).toFixed(2) },
            { key: "kmh", label: speedHead(sport), num: true, render: (r) => speedCell(sport, r.kmh / 3.6) },
            { key: "hr", label: "HR", num: true, render: (r) => num(r.hr) },
            { key: "climb_m", label: "Climb", num: true, render: (r) => num(r.climb_m) },
            { key: "grade_pct", label: "Grade", num: true, render: (r) => (r.grade_pct === null ? "—" : `${r.grade_pct.toFixed(1)} %`) },
          ], legs, { hr: (r) => r.hr })} />
          <Table rows={data.pauses} rowKey={(_r, i) => String(i)} maxHeight="420px" empty="No pauses." onHover={(r) => {
            if (!r || r.lat === null) { setCursor(null); return; }
            setCursor([r.lon!, r.lat]);
          }} cols={[
            { key: "n", label: "Pause", num: true, render: (r) => data.pauses.indexOf(r) + 1, sort: (r) => r.t },
            { key: "t", label: "At", num: true, render: (r) => dur(r.t) },
            { key: "km", label: "At km", num: true, render: (r) => r.km.toFixed(2) },
            { key: "dur_s", label: "Duration", num: true, render: (r) => dur(r.dur_s) },
            { key: "timer", label: "Timer", render: (r) => (r.timer ? "stopped" : "running") },
          ]} />
        </div>
      )}
      {tab === "laps" && (
        <Table rows={data.laps} rowKey={(_r, i) => String(i)} cols={lapCols(sport, data.laps)} maxHeight="420px"
          onHover={(r) => setSpan(r && r.start !== null ? [r.start, r.start + (r.time ?? 0)] : null)} />
      )}
    </section>
  );
}

export function ActivityPage({ id }: { id: string }) {
  const { query } = useLocation();
  const colorBy = (query.get("color") as ColorBy) || "speed";
  const { data, error } = useApi<ActivityDetail>(`/api/activities/${id}`);
  const tr = useApi<Track>(data?.activity.track ? `/api/activities/${id}/track?format=columns` : null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [span, setSpan] = useState<[number, number] | null>(null);
  const chartCursor = useRef<((i: number | null) => void) | null>(null);

  useEffect(() => { setCursor(null); setSpan(null); }, [id]);
  useEffect(() => { document.title = data ? `${data.activity.date} ${data.activity.name ?? ""}` : "Activity"; }, [data]);

  const s: Series | null = data?.series ?? null;
  const a = data?.activity;
  const swim = data?.swim ?? null;
  // pace on foot (run, walk, hike), speed for ski / bike / other (lib/format onFoot)
  const panels0: PanelKey[] = swim ? ["pace100", "hr", "cad"] : a?.sport === "run" ? ["pace", "hr", "alt", "cad", "power"] : onFoot(a?.sport) ? ["paceSlow", "hr", "alt"] : ["speed", "hr", "alt"];
  // an untrusted distance (indoor / no GPS) makes the pace / speed series nonsense: keep HR / cadence only
  const panels = a?.dist_trusted === false ? panels0.filter((p) => !["pace", "paceSlow", "speed"].includes(p)) : panels0;
  const drawable = useMemo(() => (s ? drawablePanels(s, panels) : []), [s, panels.join(",")]);
  const bands = useMemo<Band[]>(() => {
    if (!data) return [];
    const b: Band[] = data.reps.filter((r) => r.start !== undefined).map((r) => ({ a: r.start!, b: r.end!, kind: "rep" as const }));
    if (!b.length) for (const p of data.pauses) b.push({ a: p.t, b: p.t + p.dur_s, kind: "pause" });
    if (!b.length && data.swim) {   // pool swim: rests = gaps ≥ 10 s between lengths
      const L = data.swim.lengths;
      for (let i = 0; i + 1 < L.length; i++) { const e = L[i].start + L[i].time; if (L[i + 1].start - e >= 10) b.push({ a: e, b: L[i + 1].start, kind: "pause" }); }
    }
    return b;
  }, [data]);

  const onChartHover = (i: number | null) => {
    if (!s || i === null || !s.lat || s.lat[i] === null) { setCursor(null); return; }
    setCursor([s.lon![i]!, s.lat[i]!]);
  };
  const onMapHover = (t: number | null) => {
    if (!s) return;
    if (t === null) { chartCursor.current?.(null); setCursor(null); return; }
    const i = nearestIdx(s.t, t);
    chartCursor.current?.(i);
    if (s.lat?.[i] != null) setCursor([s.lon![i]!, s.lat[i]!]);
  };

  if (error) return (
    <main>
      <PageHeader title="Activity not found" />
      <div class="empty-state">
        <p><b>{/no such activity|404/i.test(error) ? `There is no activity ${id} for ${currentPerson()[0].toUpperCase() + currentPerson().slice(1)}.` : "This activity could not be loaded."}</b></p>
        <p class="muted">{/no such activity|404/i.test(error) ? "It may belong to someone else (switch person at the top left), or the link is old." : `The server said: ${error}`}</p>
        <a class="btn" href="/activities">All activities</a> <a class="btn" href="/">Home</a>
      </div>
    </main>
  );
  if (!data || !a) return <main><div class="empty">Loading…</div></main>;
  const g = data.group;
  const sport = a.sport;
  const hasReps = data.reps.some((r) => r.start !== undefined);
  const hasMap = a.track;
  const hasCharts = drawable.length > 0;
  const shaded = hasReps ? "reps shaded" : bands.length ? (swim ? "rests shaded" : "pauses shaded") : "";
  const startDiffers = a.start_place && a.start_place !== a.place;

  return (
    <main>
      <section class="first">
        <PageHeader title={<><SportDot sport={sport} />{a.name ?? a.type}</>} sub={`${longDate(a.date)} ${time(a.start)}`}
          about="One workout: the track coloured by pace, heart rate or elevation, the charts, the reps or laps, what it says about your shape, and the most similar workouts."
          tips={["Hover the chart to move a marker on the map, or hover the track to move the chart cursor", "Hover a rep / leg / lap row to see that stretch on the map",
            "Compare lines this workout up with similar ones", "← Older / Newer → step through your activities"]}>
          {a.together && (
            <a class="tg-link" href={`/community/${a.together.gid}`} title="Who did it with you, and who was faster">
              👥 {togetherWith(a.together)}{a.together.kind === "intervals" ? " · see results" : ""} ›</a>
          )}
          <a class="btn" href={`/day/${a.date}`}>Day view</a>
          {a.fit && <a class="btn" href={`/compare?suggest=${a.id}`} title="Compare with similar sessions (same workout / route)">Compare</a>}
          <a class="btn" href={data.older ? `/activities/${data.older}` : undefined} aria-disabled={!data.older} title="Older activity">← Older</a>
          <a class="btn" href={data.newer ? `/activities/${data.newer}` : undefined} title="Newer activity">Newer →</a>
        </PageHeader>
        <dl class="kv">
          <dt>Type</dt><dd>{typeText(a)}{a.type_detail ? <span class="muted"> · {a.type_detail}</span> : null}</dd>
          <dt>Place</dt><dd>{a.place ? <a href={placeHref(a.place)}>{a.place_name ?? a.place}</a> : a.location ?? "—"}
            {startDiffers ? <span class="muted"> · start <a href={placeHref(a.start_place!)}>{a.start_place_name ?? a.start_place}</a></span> : a.region && a.region !== a.place_name ? <span class="muted"> · {a.region}</span> : null}</dd>
          <dt>Distance</dt><dd>{a.dist_trusted === false ? <><DistCell a={a} unit=" km" d={1} /><span class="muted" title={a.dist_note ?? ""}>{a.dist_est_km ? " estimated from the laps" : " (no GPS: the watch's distance isn't trusted)"} · watch said {a.dist_km?.toFixed(2) ?? "—"} km</span></>
            : a.dist_km ? `${a.dist_km.toFixed(2)} km` : "—"}</dd>
          <dt>Route</dt><dd title={data.route?.name}>{data.route ? <><a href={`/places?route=${data.route.id}`}>{data.route.id} {data.route.name}</a> <span class="muted">{g.route_dir === "rev" ? "reverse direction, " : ""}{data.route.n} sessions</span>
            {data.route.rank && <span title="time on the route: moving time from the route's start line to its end (lead-in excluded)">{" · "}{data.route.rank.is_pb ? <span class="pr-chip">🏆 fastest of {data.route.rank.of}</span>
              : <>{fastest(data.route.rank.rank)} of {data.route.rank.of} <span class="muted">({dur(data.route.rank.route_time_s)} on the route, best <a href={`/activities/${data.route.rank.best_id}`}>{dur(data.route.rank.best_s)}</a>)</span></>}</span>}</> : "—"}</dd>
          {swim ? <><dt>Swimming / rest</dt><dd>{dur(swim.active_s)} / {dur(swim.rest_s)} <span class="muted">{swim.n_rests ?? 0} rests{swim.longest_rest_s ? `, longest ${dur(swim.longest_rest_s)}` : ""}; pace from swimming time</span></dd></>
            : <><dt>Moving / paused</dt><dd>{dur(g.moving_s ?? a.moving_s)} / {dur(g.pause_s)} <span class="muted">{g.n_pauses ?? 0} pauses{g.longest_pause_s ? `, longest ${dur(g.longest_pause_s)}` : ""}</span></dd></>}
          <dt>Workout</dt><dd title={workoutTip(a)}>{a.workout_label ?? typeText(a)}{a.rep_pace_ms ? <span class="muted" title="average rep pace = total rep distance / total rep time of the main reps"> · avg rep pace {speedOf(sport, a.rep_pace_ms)}</span> : null}</dd>
          <dt>{onFoot(sport) || swim ? "Pace" : "Speed"}</dt><dd>{speedText(a)}{swim ? null : <span class="muted">{a.dist_trusted === false ? " no trusted distance, so no pace (see the reps)" : " moving"}</span>}</dd>
          <dt>Heart rate</dt><dd>{a.hr === null ? "—" : <>{num(a.hr)} bpm avg, {num(a.maxhr)} max <span class="muted">whole activity</span></>}</dd>
          {swim ? <>
            <dt>Pool</dt><dd>{swim.pool_m ? `${num(swim.pool_m)} m` : "—"} <span class="muted">{swim.n_lengths ?? swim.lengths?.length} lengths{swim.bad_lengths ? `, ${swim.bad_lengths} glitch lengths dropped` : ""}</span></dd>
            <dt>Strokes</dt><dd>{swim.strokes_per_length ? `${num(swim.strokes_per_length, 1)} per length` : "—"} <span class="muted">timer {dur(swim.timer_s)}, elapsed {dur(swim.elapsed_s)}</span></dd>
          </> : <>
            <dt>Climb</dt><dd>{a.elev !== null ? `${a.elev} m` : "—"}</dd>
            <dt title="Garmin training load / aerobic training effect (0–5)">Training load / effect</dt><dd>{num(a.load)} / {a.te ?? "—"}</dd>
          </>}
        </dl>
        <div class="act-chips"><RecordChips id={id} /></div>
        <WorkoutProgress id={id} />
      </section>

      {(hasMap || hasCharts) ? (
        <section>
          <div class="head">
            <h2>{hasMap ? "Map and series" : "Series"}</h2>
            <span class="sub">{[shaded, hasMap && hasCharts ? "hover the chart or the track" : "", !hasMap ? "no GPS track" : ""].filter(Boolean).join("; ")}</span>
            {hasMap && (
              <div class="tools">
                <span class="muted">Colour by</span>
                <div class="seg">
                  {(["speed", "hr", "alt"] as ColorBy[]).map((c) => (
                    <button key={c} class={colorBy === c ? "on" : ""} onClick={() => setQuery({ color: c === "speed" ? null : c })}>
                      {c === "speed" ? (onFoot(sport) ? "Pace" : "Speed") : c === "hr" ? "Heart rate" : "Elevation"}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div class={hasMap && hasCharts ? "act-top" : ""}>
            {hasMap && (tr.data ? <ActivityMap track={tr.data} colorBy={colorBy} sport={sport} pauses={data.pauses} cursor={cursor} span={span} onHover={onMapHover} />
              : <div class="map bordered"><div class="msg">{tr.error ?? "Loading track…"}</div></div>)}
            {hasCharts && <div><SeriesCharts series={s!} panels={drawable} bands={bands} onHover={onChartHover} cursorRef={chartCursor} /></div>}
          </div>
        </section>
      ) : (
        <section>
          <p class="note">{a.fit ? "No GPS track and too little per-second data for charts." : "Watch file not downloaded: only Garmin's summary is available (no track, charts or segments)."}</p>
        </section>
      )}

      <Segments data={data} sport={sport} setSpan={setSpan} setCursor={setCursor} />

      <ShapeSection id={id} />

      <NearestPanel id={id} />
    </main>
  );
}
