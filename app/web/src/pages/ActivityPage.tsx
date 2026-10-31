// One activity: map with the track coloured by speed / HR / elevation, stacked charts with linked hover
// (chart <-> map), reps, laps, legs and pauses. Hovering a table row highlights that stretch on the map.
import type { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import { Popup } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useApi, type ActivityDetail, type Series } from "../lib/api";
import { setQuery, useLocation } from "../lib/router";
import { dur, num, pace, kmh, stypeLabel, time, dayLabel } from "../lib/format";
import { RAMP, quantile } from "../lib/geo";
import { MapView, setData, fitBBox, EMPTY } from "../components/MapView";
import { SeriesCharts, type Band, type PanelKey } from "../components/SeriesCharts";
import { Table, type Col } from "../components/Table";
import { SportDot, speedText, what } from "../components/ActivityBits";
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

  const fmt = (v: number) => colorBy === "speed" ? (sport === "run" ? pace(v) + " /km" : kmh(v) + " km/h") : colorBy === "hr" ? `${Math.round(v)} bpm` : `${Math.round(v)} m`;
  return (
    <MapView onReady={onReady} class="bordered">
      <div class="overlay bl ramp">
        <span>{fmt(lo)}</span><i style={{ background: `linear-gradient(90deg, ${RAMP.join(",")})` }} /><span>{fmt(hi)}</span>
      </div>
    </MapView>
  );
}

const lapCols = (sport: string): Col<any>[] => [
  { key: "n", label: "Lap", num: true, render: (_r, i) => i + 1, sort: (r) => r.start },
  { key: "start", label: "Start", num: true, render: (r) => dur(r.start) },
  { key: "time", label: "Time", num: true, render: (r) => dur(r.timer ?? r.time) },
  { key: "dist", label: "Dist (m)", num: true, render: (r) => num(r.dist) },
  { key: "speed", label: sport === "run" ? "Pace" : "km/h", num: true, render: (r) => (sport === "run" ? pace(r.speed) : kmh(r.speed)) },
  { key: "hr", label: "Avg HR", num: true, render: (r) => num(r.hr) },
  { key: "maxhr", label: "Max HR", num: true, render: (r) => num(r.maxhr) },
  { key: "climb", label: "Climb", num: true, render: (r) => num(r.climb) },
  { key: "intensity", label: "Intensity", render: (r) => r.intensity ?? "—" },
  { key: "trigger", label: "Trigger", render: (r) => r.trigger ?? "—" },
];

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
  const panels: PanelKey[] = a?.sport === "run" ? ["pace", "hr", "alt", "cad", "power"] : ["speed", "hr", "alt"];
  const bands = useMemo<Band[]>(() => {
    if (!data) return [];
    const b: Band[] = data.reps.filter((r) => r.start !== undefined).map((r) => ({ a: r.start!, b: r.end!, kind: "rep" as const }));
    if (!b.length) for (const p of data.pauses) b.push({ a: p.t, b: p.t + p.dur_s, kind: "pause" });
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

  if (error) return <main><h1>Activity {id}</h1><div class="err">{error}</div></main>;
  if (!data || !a) return <main><div class="empty">Loading…</div></main>;
  const g = data.group;
  const sport = a.sport;
  const hasReps = data.reps.some((r) => r.start !== undefined);

  return (
    <main>
      <section class="first">
        <div class="head">
          <h1><SportDot sport={sport} />{a.name ?? a.type}</h1>
          <span class="sub">{dayLabel(a.date)} {time(a.start)}</span>
          <div class="tools">
            <a class="btn" href={`/day/${a.date}`}>Day view</a>
            {a.fit && <a class="btn" href={`/compare?suggest=${a.id}`} title="Compare with similar sessions (same workout / route)">Compare</a>}
            <a class="btn" href={data.older ? `/activities/${data.older}` : undefined} aria-disabled={!data.older} title="Older activity">← Older</a>
            <a class="btn" href={data.newer ? `/activities/${data.newer}` : undefined} title="Newer activity">Newer →</a>
          </div>
        </div>
        <dl class="kv">
          <dt>Type</dt><dd>{what(a)}{a.type_detail ? ` (${a.type_detail})` : ""}</dd>
          <dt>Place</dt><dd>{a.place ? <a href={`/places?place=${encodeURIComponent(a.place)}`}>{a.place}</a> : a.location ?? "—"}{a.area ? <span class="muted"> area {a.area}</span> : null}</dd>
          <dt>Distance</dt><dd>{a.dist_km ? `${a.dist_km.toFixed(2)} km` : "—"}{g.dist_trusted === false ? <span class="muted"> (no GPS, not trusted)</span> : null}</dd>
          <dt>Route</dt><dd>{data.route ? <><a href={`/places?route=${data.route.id}`}>{data.route.id} {data.route.name}</a> <span class="muted">{g.route_dir ?? ""}, {data.route.n} sessions</span></> : "—"}</dd>
          <dt>Moving / paused</dt><dd>{dur(g.moving_s ?? a.moving_s)} / {dur(g.pause_s)} <span class="muted">{g.n_pauses ?? 0} pauses{g.longest_pause_s ? `, longest ${dur(g.longest_pause_s)}` : ""}</span></dd>
          <dt>Workout</dt><dd>{a.sig ?? stypeLabel(a.stype) ?? "—"}</dd>
          <dt>Pace / speed</dt><dd>{speedText(a)}</dd>
          <dt>Heart rate</dt><dd>{num(a.hr)} avg, {num(a.maxhr)} max</dd>
          <dt>Climb</dt><dd>{a.elev !== null ? `${a.elev} m` : "—"}</dd>
          <dt>Load / TE</dt><dd>{num(a.load)} / {a.te ?? "—"}</dd>
        </dl>
      </section>

      <section>
        <div class="head">
          <h2>Map and series</h2>
          <span class="sub">{hasReps ? "Reps shaded" : bands.length ? "Pauses shaded" : ""}{a.track ? "; hover the chart or the track" : ""}</span>
          <div class="tools">
            <span class="muted">Colour by</span>
            <div class="seg">
              {(["speed", "hr", "alt"] as ColorBy[]).map((c) => (
                <button key={c} class={colorBy === c ? "on" : ""} onClick={() => setQuery({ color: c === "speed" ? null : c })}>
                  {c === "speed" ? (sport === "run" ? "Pace" : "Speed") : c === "hr" ? "Heart rate" : "Elevation"}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div class="act-top">
          {a.track ? (tr.data ? <ActivityMap track={tr.data} colorBy={colorBy} sport={sport} pauses={data.pauses} cursor={cursor} span={span} onHover={onMapHover} />
            : <div class="map bordered"><div class="msg">{tr.error ?? "Loading track…"}</div></div>)
            : <div class="map bordered" style={{ height: "200px" }}><div class="msg">No GPS for this activity{a.fit ? "" : " (watch file not downloaded)"}</div></div>}
          <div>{s ? <SeriesCharts series={s} panels={panels} bands={bands} onHover={onChartHover} cursorRef={chartCursor} />
            : <div class="empty">No per-second data (watch file not downloaded).</div>}</div>
        </div>
      </section>

      {hasReps && (
        <section>
          <div class="head"><h2>Reps</h2><span class="count">{data.reps.length}</span><span class="sub">from the rep detector (run_analysis.py); hover a row to see it on the map</span></div>
          <Table rows={data.reps} rowKey={(_r, i) => String(i)} onHover={(r) => setSpan(r ? [r.start!, r.end!] : null)} cols={[
            { key: "n", label: "Rep", num: true, render: (_r, i) => i + 1, sort: (r) => r.start },
            { key: "start", label: "Start", num: true, render: (r) => dur(r.start) },
            { key: "time", label: "Time", num: true, render: (r) => dur(r.time) },
            { key: "dist", label: "Dist (m)", num: true, render: (r) => num(r.dist) },
            { key: "speed", label: sport === "run" ? "Pace" : "km/h", num: true, render: (r) => (sport === "run" ? pace(r.speed) : kmh(r.speed)) },
            { key: "hr", label: "Avg HR", num: true, render: (r) => num(r.hr) },
            { key: "maxhr", label: "Max HR", num: true, render: (r) => num(r.maxhr) },
            { key: "brk", label: "Break after", num: true, render: (_r, i) => dur(data.breaks[i]?.time), sort: () => null },
          ]} />
        </section>
      )}

      <ShapeSection id={id} />

      {data.laps.length > 1 && (
        <section>
          <div class="head"><h2>Laps</h2><span class="count">{data.laps.length}</span><span class="sub">from the watch file</span></div>
          <Table rows={data.laps} rowKey={(_r, i) => String(i)} cols={lapCols(sport)} maxHeight="420px"
            onHover={(r) => setSpan(r && r.start !== null ? [r.start, r.start + (r.time ?? 0)] : null)} />
        </section>
      )}

      {(data.legs.length > 0 || data.pauses.length > 0) && (
        <section>
          <div class="head"><h2>Legs & pauses</h2><span class="sub">{data.legs.length} legs, {data.pauses.length} pauses ≥ 15 s (session_groups.py)</span></div>
          <div class="cols" style={{ gridTemplateColumns: "3fr 2fr" }}>
            <Table rows={data.legs} rowKey={(_r, i) => String(i)} maxHeight="420px" onHover={(r) => setSpan(r ? [r.t, r.t + r.dur_s] : null)} cols={[
              { key: "n", label: "Leg", num: true, render: (_r, i) => i + 1, sort: (r) => r.t },
              { key: "t", label: "Start", num: true, render: (r) => dur(r.t) },
              { key: "dur_s", label: "Time", num: true, render: (r) => dur(r.dur_s) },
              { key: "dist_m", label: "km", num: true, render: (r) => (r.dist_m / 1000).toFixed(2) },
              { key: "kmh", label: sport === "run" ? "Pace" : "km/h", num: true, render: (r) => (sport === "run" ? pace(r.kmh / 3.6) : r.kmh.toFixed(1)) },
              { key: "hr", label: "HR", num: true, render: (r) => num(r.hr) },
              { key: "climb_m", label: "Climb", num: true, render: (r) => num(r.climb_m) },
              { key: "grade_pct", label: "Grade", num: true, render: (r) => (r.grade_pct === null ? "—" : `${r.grade_pct.toFixed(1)} %`) },
            ]} />
            <Table rows={data.pauses} rowKey={(_r, i) => String(i)} maxHeight="420px" empty="No pauses." onHover={(r) => {
              if (!r || r.lat === null) { setCursor(null); return; }
              setCursor([r.lon!, r.lat]);
            }} cols={[
              { key: "n", label: "Pause", num: true, render: (_r, i) => i + 1, sort: (r) => r.t },
              { key: "t", label: "At", num: true, render: (r) => dur(r.t) },
              { key: "km", label: "At km", num: true, render: (r) => r.km.toFixed(2) },
              { key: "dur_s", label: "Duration", num: true, render: (r) => dur(r.dur_s) },
              { key: "timer", label: "Timer", render: (r) => (r.timer ? "stopped" : "running") },
            ]} />
          </div>
        </section>
      )}

      <NearestPanel id={id} />
    </main>
  );
}
