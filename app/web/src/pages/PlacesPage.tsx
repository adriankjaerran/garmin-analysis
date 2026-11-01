// Places (start-point clusters) and routes (repeated tracks) from groups.json, on a map.
// ?place=<id> filters routes to that place, ?route=R3 shows the route: reference line, member tracks, sessions.
import type { Map as MLMap, MapLayerMouseEvent } from "maplibre-gl";
import { Marker, Popup } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { qs, useApi, type Activity, type Place, type RouteDetail, type RouteRow } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { SPORT_LABEL, dur, km, num, sportColor, typeLabel } from "../lib/format";
import { decodePolyline, type BBox } from "../lib/geo";
import { MapView, setData, fitBBox, EMPTY } from "../components/MapView";
import { Table, type Col } from "../components/Table";
import { DistCell, SportDot, TogetherMark, distSort, placeHref, speedSort, speedText, whatShort } from "../components/ActivityBits";
import { PageHeader } from "../components/PageHeader";

const SHAPE: Record<string, string> = { laps: "laps", out_and_back: "out & back", point_to_point: "point to point", loop_cw: "loop ↻", loop_ccw: "loop ↺" };
const famOf = (types: Record<string, number>) => {
  const top = Object.entries(types).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "other";
  return top.includes("ski") ? "ski" : top.includes("run") ? "run" : top === "hiking" || top === "walking" ? "hike" : top === "cycling" ? "bike" : top.includes("swim") ? "swim" : "other";
};

/** "94 · 108 touched": main-place activities, plus all that start, end or pass there when that's more */
const countText = (p: Place) => (p.n_touched && p.n_touched > p.n ? <>{p.n} <span class="muted">· {p.n_touched} touched</span></> : String(p.n));
const COUNT_HELP = "Activities with this as their main place (≥ 40 % of the time there, else the start) · activities that start, end or spend ≥ 5 % / 3 min there";

const routeCols: Col<RouteRow>[] = [
  { key: "id", label: "Route", sort: (r) => Number(r.id.slice(1)) },
  { key: "name", label: "Name", render: (r) => <span class="clip" style={{ maxWidth: "255px" }} title={[r.name, r.via_names?.length ? `via ${r.via_names.join(", ")}` : ""].filter(Boolean).join(" · ")}><SportDot sport={r.sport} />{r.name}</span> },
  { key: "n", label: "Activities", num: true },
  { key: "pb", label: "Best", num: true, sort: (r) => r.pb?.route_time_s ?? null, title: "fastest time on the route (main direction; laps routes aren't ranked)",
    render: (r) => (r.pb ? <span title={r.pb.best_date ?? ""}>{dur(r.pb.route_time_s)}</span> : <span class="muted">—</span>) },
  { key: "last", label: "Last", render: (r) => r.last ?? "—" },
];
/** time on the route per session, oldest left; lower = faster (drawn upward), the PB ringed */
function RouteTrend({ pts }: { pts: { date: string | null; route_time_s: number }[] }) {
  if (pts.length < 2) return null;
  const w = 160, h = 26, v = pts.map((p) => p.route_time_s), lo = Math.min(...v), hi = Math.max(...v), rg = hi - lo || 1;
  const x = (i: number) => 3 + (i / (pts.length - 1)) * (w - 6), y = (t: number) => 3 + ((t - lo) / rg) * (h - 6);
  const bi = v.indexOf(lo);
  return (
    <svg class="spark" width={w} height={h} style={{ display: "inline-block", verticalAlign: "middle" }} role="img">
      <polyline points={pts.map((p, i) => `${x(i).toFixed(1)},${y(p.route_time_s).toFixed(1)}`).join(" ")} fill="none" stroke="var(--accent)" stroke-width={1.5} />
      {pts.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.route_time_s)} r={1.8} fill="var(--accent)"><title>{p.date}: {dur(p.route_time_s)}</title></circle>)}
      <circle cx={x(bi)} cy={y(lo)} r={4} fill="none" stroke="var(--state-good)" stroke-width={1.5} />
    </svg>
  );
}
const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
type Member = RouteDetail["members"][number];
const placeCols: Col<Place & { fam: string }>[] = [
  { key: "name", label: "Place", render: (p) => <span class="clip" style={{ maxWidth: "255px" }} title={p.name}><SportDot sport={p.fam} />{p.name}</span> },
  { key: "n", label: "Activities", num: true, render: countText, title: COUNT_HELP },
  { key: "routes", label: "Routes", num: true },
  { key: "last", label: "Last", render: (p) => p.last ?? "—" },
];

export function PlacesPage() {
  const { query } = useLocation();
  const place = query.get("place"), route = query.get("route"), sport = query.get("sport") || "";
  const tabQ = query.get("tab");
  const tab = tabQ === "places" ? "places" : tabQ === "activities" && place ? "activities" : "routes";
  const pl = useApi<{ places: Place[] }>("/api/places").data;
  const rs = useApi<{ routes: RouteRow[] }>("/api/routes" + qs({ sport: sport || null })).data;
  const rd = useApi<RouteDetail>(route ? `/api/routes/${route}` : null);
  const [map, setMap] = useState<MLMap | null>(null);
  const [hlMember, setHlMember] = useState<string | null>(null);
  const placesRef = useRef<(Place & { fam: string })[]>([]);

  const places = useMemo(() => (pl?.places ?? []).map((p) => ({ ...p, fam: famOf(p.types) })), [pl]);
  placesRef.current = places;
  const routes = useMemo(() => (rs?.routes ?? []).filter((r) => !place || r.place === place), [rs, place]);
  const selPlace = places.find((p) => p.id === place) ?? null;
  const placeActs = useApi<{ n: number; activities: Activity[] }>(place && tab === "activities" ? `/api/activities?place=${encodeURIComponent(place)}` : null).data;
  const actCols = useMemo<Col<Activity>[]>(() => [
    { key: "start", label: "Date", render: (a) => a.date },
    { key: "type", label: "Workout", render: (a) => <span class="clip" style={{ maxWidth: "220px" }} title={[whatShort(a), a.route_name, a.name].filter(Boolean).join(" · ")}><SportDot sport={a.sport} /><TogetherMark a={a} />{whatShort(a)}</span>, sort: (a) => whatShort(a) },
    { key: "dist_km", label: "km", num: true, render: (a) => <DistCell a={a} />, sort: distSort },
    { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s ?? a.dur_s) },
    { key: "speed", label: "Pace / speed", num: true, render: speedText, sort: speedSort },
  ], []);
  const pinRef = useRef<Marker[]>([]);

  const onReady = (m: MLMap) => {
    for (const s of ["members", "ref", "wps", "places", "hl"]) setData(m, s, EMPTY);
    m.addLayer({ id: "members", type: "line", source: "members", layout: { "line-join": "round", "line-cap": "round" }, paint: { "line-color": ["get", "color"], "line-width": 2, "line-opacity": 0.35 } });
    m.addLayer({ id: "hl", type: "line", source: "hl", layout: { "line-join": "round", "line-cap": "round" }, paint: { "line-color": "#1d2125", "line-width": 4, "line-opacity": 0.85 } });
    m.addLayer({ id: "ref", type: "line", source: "ref", layout: { "line-join": "round", "line-cap": "round" }, paint: { "line-color": "#1d2125", "line-width": 2.5, "line-dasharray": [2, 1.5] } });
    m.addLayer({ id: "wps", type: "circle", source: "wps", paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#1d2125", "circle-stroke-width": 2 } });
    m.addLayer({ id: "places", type: "circle", source: "places", paint: {
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 4, 16, 16],
      "circle-color": ["get", "color"], "circle-opacity": ["case", ["get", "sel"], 0.95, 0.55],
      "circle-stroke-color": ["case", ["get", "sel"], "#1d2125", "#fff"], "circle-stroke-width": ["case", ["get", "sel"], 2.5, 1.5] } });
    const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 12 });
    m.on("mousemove", "places", (e: MapLayerMouseEvent) => {
      const p = placesRef.current.find((x) => x.id === e.features?.[0]?.properties?.id);
      if (!p) return;
      m.getCanvas().style.cursor = "pointer";
      popup.setLngLat([p.lon, p.lat]).setHTML(`<b>${p.name}</b><br>${p.n} activities${p.n_touched && p.n_touched > p.n ? ` (${p.n_touched} touched)` : ""}, ${p.routes} routes<br>${Object.entries(p.types).map(([k, v]) => `${typeLabel(k)} ${v}`).join(", ")}`).addTo(m);
    });
    m.on("mouseleave", "places", () => { m.getCanvas().style.cursor = ""; popup.remove(); });
    m.on("click", "places", (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id;
      if (id) setQuery({ place: id, route: null, tab: null });
    });
    m.on("mousemove", "wps", (e: MapLayerMouseEvent) => {
      const q = e.features?.[0]?.properties as any;
      popup.setLngLat(e.lngLat).setHTML(`<b>${q.name}</b> at ${Number(q.km).toFixed(1)} km<br>${Math.round(q.share * 100)} % of sessions stop here`).addTo(m);
    });
    m.on("mouseleave", "wps", () => popup.remove());
    setMap(m);
  };

  useEffect(() => {
    if (!map) return;
    setData(map, "places", { type: "FeatureCollection", features: places.map((p) => ({ type: "Feature",
      properties: { id: p.id, n: p.n, color: sportColor(p.fam), sel: p.id === place || (rd.data?.place === p.id && !!route) },
      geometry: { type: "Point", coordinates: [p.lon, p.lat] } })) });
  }, [map, places, place, route, rd.data]);

  // name labels for the busiest places (the raster style has no glyphs, so these are HTML markers). Placed
  // greedily, busiest first: right of the circle, else left / above / below; a label that would overlap a placed
  // label or another busy circle is left out (its name is in the hover popup). Re-placed after every move / zoom.
  useEffect(() => {
    if (!map) return;
    const top = [...places].sort((a, b) => b.n - a.n).slice(0, 14).filter((p) => p.n >= 5);
    const rad = (n: number) => 4 + ((Math.min(16, Math.sqrt(n)) - 1) / 15) * 12;   // = the circle radius expression
    const place = () => {
      pinRef.current.forEach((m) => m.remove());
      pinRef.current = [];
      const boxes: [number, number, number, number][] = [];
      const circles = [...places].sort((a, b) => b.n - a.n).slice(0, 60).map((p) => { const q = map.project([p.lon, p.lat]); return [q.x, q.y, rad(p.n) + 2] as const; });
      const hit = (b: [number, number, number, number]) => boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]) ||
        circles.some(([cx, cy, r]) => cx + r > b[0] && cx - r < b[2] && cy + r > b[1] && cy - r < b[3]);
      for (const p of top) {
        const q = map.project([p.lon, p.lat]), r = rad(p.n) + 4, w = p.name.length * 6.6 + 10, h = 18;
        const opts: [string, [number, number], [number, number, number, number]][] = [
          ["left", [r, 0], [q.x + r, q.y - h / 2, q.x + r + w, q.y + h / 2]],
          ["right", [-r, 0], [q.x - r - w, q.y - h / 2, q.x - r, q.y + h / 2]],
          ["bottom", [0, -r], [q.x - w / 2, q.y - r - h, q.x + w / 2, q.y - r]],
          ["top", [0, r], [q.x - w / 2, q.y + r, q.x + w / 2, q.y + r + h]],
        ];
        // its own circle doesn't count as a collision
        const own = circles.findIndex(([cx, cy]) => Math.abs(cx - q.x) < 0.5 && Math.abs(cy - q.y) < 0.5);
        const saved = own >= 0 ? circles.splice(own, 1) : [];
        const ok = opts.find((o) => !hit(o[2]));
        circles.push(...saved);
        if (!ok) continue;
        boxes.push(ok[2]);
        const el = document.createElement("div");
        el.className = "place-label";
        el.textContent = p.name;
        el.title = `${p.name}: ${p.n} activities`;
        pinRef.current.push(new Marker({ element: el, anchor: ok[0] as any, offset: ok[1] }).setLngLat([p.lon, p.lat]).addTo(map));
      }
    };
    place();
    map.on("moveend", place);
    return () => { map.off("moveend", place); pinRef.current.forEach((m) => m.remove()); };
  }, [map, places]);

  // route on the map
  useEffect(() => {
    if (!map) return;
    const r = route ? rd.data : null;
    if (!r) { for (const s of ["members", "ref", "wps"]) setData(map, s, EMPTY); return; }
    const color = sportColor(r.sport);
    setData(map, "members", { type: "FeatureCollection", features: r.members.filter((m) => m.poly).map((m) => ({
      type: "Feature", properties: { id: m.id, color }, geometry: { type: "LineString", coordinates: decodePolyline(m.poly!) } })) });
    const ref = r.ref ? r.ref.lon.map((x, i) => [x, r.ref!.lat[i]]) : [];
    setData(map, "ref", ref.length ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ref } } : EMPTY);
    setData(map, "wps", { type: "FeatureCollection", features: (r.waypoints ?? []).filter((w) => w.lat).map((w) => ({
      type: "Feature", properties: { name: w.name, km: w.km, share: w.share_stopped }, geometry: { type: "Point", coordinates: [w.lon, w.lat] } })) });
    if (ref.length) {
      const xs = ref.map((c) => c[0]), ys = ref.map((c) => c[1]);
      fitBBox(map, [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], { padding: 50, animate: true });
    }
  }, [map, rd.data, route]);

  // fit to place / all places
  useEffect(() => {
    if (!map || route || !places.length) return;
    if (selPlace) { map.flyTo({ center: [selPlace.lon, selPlace.lat], zoom: Math.max(map.getZoom(), 12) }); return; }
    const big = places.filter((p) => p.n >= 3);
    const lons = big.map((p) => p.lon).sort((a, b) => a - b), lats = big.map((p) => p.lat).sort((a, b) => a - b);
    const mlon = lons[lons.length >> 1], mlat = lats[lats.length >> 1];
    const near = big.filter((p) => Math.abs(p.lon - mlon) < 1 && Math.abs(p.lat - mlat) < 0.5);
    if (near.length) fitBBox(map, [Math.min(...near.map((p) => p.lon)), Math.min(...near.map((p) => p.lat)), Math.max(...near.map((p) => p.lon)), Math.max(...near.map((p) => p.lat))] as BBox, { padding: 40 });
  }, [map, places.length, place, route]);

  useEffect(() => {
    if (!map) return;
    const m = rd.data?.members.find((x) => x.id === hlMember);
    setData(map, "hl", m?.poly ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: decodePolyline(m.poly) } } : EMPTY);
  }, [map, hlMember, rd.data]);

  const r = route ? rd.data : null;
  // the main ranking group (a point-to-point route ranks each direction on its own): the PB and trend shown
  const mainPb = r?.pbs?.length ? r.pbs.reduce((a, b) => (b.of > a.of ? b : a)) : null;
  const trendPts = (r?.trend ?? []).filter((t) => t.group === mainPb?.group);
  const legendSports = [...new Set(places.map((p) => p.fam))].sort();
  return (
    <main class="full">
      <PageHeader class="toolbar" title="Places & routes" sub={`${places.length} places, ${rs?.routes.length ?? 0} routes you have done at least twice`}
        about="Where you train and the routes you repeat. Pick a place for its routes and activities, or a route to see every time you ran it."
        tips={["Click a circle on the map (or a row) to select that place", "A route shows its reference line (dashed), common stops and every session", "“Compare all” lines the sessions of a route up in Compare"]}>
        <select value={sport} onChange={(e) => setQuery({ sport: (e.target as HTMLSelectElement).value })}>
          <option value="">All sports</option>
          {["run", "ski", "hike", "bike"].map((s) => <option key={s} value={s}>{SPORT_LABEL[s]}</option>)}
        </select>
        {(place || route) && <button onClick={() => setQuery({ place: null, route: null })}>Show all</button>}
      </PageHeader>
      <div class="split">
        <div class="left"><MapView style={{ flex: 1 }} onReady={onReady} defaultBasemap="grey">
          {!r && <div class="overlay bl places-legend">
            <span><b>Places</b> by main sport:</span>
            {legendSports.map((k) => <span key={k}><SportDot sport={k} />{SPORT_LABEL[k]}</span>)}
            <span class="muted">· size = activities there</span>
          </div>}
        </MapView></div>
        <div class="right" style={{ width: "560px" }}>
          {r ? (
            <>
              <div class="pane">
                <div class="head">
                  <h2 title={r.name}>{r.id} {r.name}</h2>
                </div>
                <div class="head">
                  <div class="tools" style={{ marginLeft: 0 }}>
                    <a class="btn" href={`/compare?ids=${r.members.slice().sort((x, y) => (x.date < y.date ? 1 : -1)).slice(0, 40).map((m) => m.id).join(",")}&mode=${r.shape === "laps" ? "moving" : "distance"}`}
                      title={r.members.length > 40 ? "The 40 most recent sessions on this route in Compare" : "Every session on this route in Compare"}>
                      Compare {r.members.length > 40 ? "latest 40" : `all ${r.members.length}`}</a>
                    <a class="btn" href={`/activities?route=${r.id}`}>List activities</a>
                    <a class="btn" href={placeHref(r.place)}>← {r.place_name ?? r.place}</a>
                  </div>
                </div>
                <dl class="kv">
                  <dt>Start</dt><dd><a href={placeHref(r.place)}>{r.start_place_name ?? r.place_name ?? r.place}</a>{r.end_place_name && r.end_place_name !== (r.start_place_name ?? r.place_name) ? ` → ${r.end_place_name}` : ""}</dd>
                  <dt>Sport</dt><dd><SportDot sport={r.sport} />{SPORT_LABEL[r.sport] ?? r.sport}</dd>
                  <dt>Shape</dt><dd>{SHAPE[r.shape] ?? r.shape}{r.via_names?.length ? <span class="muted"> via {r.via_names.join(", ")}</span> : null}</dd>
                  <dt>Distance</dt><dd>{km(r.dist_km)} km{r.climb_m ? <span class="muted"> · {num(r.climb_m)} m climb</span> : null}</dd>
                  <dt>Activities</dt><dd>{r.n}</dd>

                  <dt>First / last</dt><dd>{r.first} / {r.last}</dd>
                </dl>
                {mainPb && <p class="route-best" title={r.rank_definition}>🏆 Best <b>{dur(mainPb.route_time_s)}</b> on the route ·{" "}
                  <a href={`/activities/${mainPb.id}`}>{mainPb.best_date}</a>
                  {r.pbs!.length > 1 ? <span class="muted"> ({mainPb.group === "rev" ? "reverse" : "main"} direction)</span> : null}{" "}
                  <span class="muted">every time, oldest left (up = faster):</span> <RouteTrend pts={trendPts} /></p>}
                <p class="note">Dashed = reference session{r.waypoints?.length ? ", circles = common stops" : ""}. Compare lines them up along the route (gap, pace, HR, legs).</p>
              </div>
              <div class="pane" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
                <div class="head"><h2>Activities</h2><span class="count">{r.members.length}</span>
                  {r.ranked && <span class="sub">ranked by time on the route (start line to end, lead-in excluded){r.pbs && r.pbs.length > 1 ? ", per direction" : ""}</span>}</div>
                <Table<Member> rows={r.members} rowKey={(a) => a.id} highlight={hlMember} onHover={(a) => setHlMember(a?.id ?? null)}
                  onRow={(a) => navigate(`/activities/${a.id}`)} defaultSort={["date", -1]} cols={[
                    ...(r.ranked ? [{ key: "rank", label: "#", num: true, title: "rank by time on the route (per direction on a point-to-point route)",
                      sort: (a: Member) => (a.rank ? (a.group === mainPb?.group ? 0 : 1000) + a.rank : null),
                      render: (a: Member) => (a.rank ? <span title={`${a.rank === 1 ? "fastest" : `${ordinal(a.rank)} fastest`} of ${a.of}${r.pbs && r.pbs.length > 1 ? ` (${a.group === "rev" ? "reverse" : "main"} direction)` : ""}`}>{a.is_pb ? "🏆" : a.rank}</span> : "—") }] : []),
                    { key: "date", label: "Date" },
                    { key: "stype", label: "Workout", render: (a: Member) => <span class="clip" style={{ maxWidth: r.ranked ? "90px" : "110px" }} title={`${whatShort(a)} · ${a.name ?? ""}`}><TogetherMark a={a} />{whatShort(a)}</span>, sort: (a: Member) => whatShort(a) },
                    ...(r.ranked ? [
                      { key: "route_time_s", label: "On route", num: true, title: "time on the route: moving time from its start line to its end (lead-in / cool-down excluded); hover for moving / paused",
                        render: (a: Member) => <span title={`moving ${dur(a.moving_s)} · paused ${dur(a.pause_s)}${a.time_src === "moving" ? " · whole moving time (no leg split)" : ""}`}>{a.route_time_s ? dur(a.route_time_s) : "—"}</span> },
                      { key: "delta_s", label: "Δ best", num: true, title: "behind the fastest time on the route (same direction)",
                        render: (a: Member) => (a.delta_s === undefined ? "—" : a.delta_s === 0 ? <span class="muted">best</span> : `+${dur(a.delta_s)}`) },
                    ] : [
                      { key: "moving_s", label: "Moving", num: true, render: (a: Member) => dur(a.moving_s) },
                      { key: "pause_s", label: "Paused", num: true, render: (a: Member) => dur(a.pause_s) },
                    ]),
                    // one sport per route: "Pace" or "Speed" (the long "Pace / speed" header pushed Dir out of the pane)
                    { key: "speed", label: r.sport === "run" || r.sport === "hike" ? "Pace" : "Speed", num: true, render: speedText, sort: speedSort },
                    { key: "hr", label: "HR", num: true, render: (a) => a.hr ?? "—" },
                    // direction only when the sessions differ (else it's a column of → that pushes HR out of the pane)
                    ...(new Set(r.members.map((m) => m.route_dir ?? "")).size > 1 ? [{ key: "route_dir", label: "Dir", title: "direction along the route: → as the reference, ← reversed",
                      render: (a: Member) => (a.route_dir === "rev" ? <span title="reversed">←</span> : a.route_dir ? <span title="same direction as the reference">→</span> : "—") }] : []),
                  ]} />
              </div>
            </>
          ) : (
            <>
              {selPlace && (
                <div class="pane">
                  <div class="head"><h2><SportDot sport={selPlace.fam} />{selPlace.name}</h2>
                    <div class="tools">
                      <a class="btn" href={`/activities?place=${encodeURIComponent(selPlace.id)}&adv=1`} title="All activities touching this place, with filters and Compare">List activities</a>
                    </div></div>
                  <dl class="kv">
                    <dt>Activities</dt><dd title={COUNT_HELP}>{countText(selPlace)}</dd>
                    <dt>Routes</dt><dd>{selPlace.routes}</dd>
                    <dt>Kind</dt><dd>{selPlace.src === "named" ? (selPlace.kind ?? "named place") : "start cluster"}</dd>
                    <dt>Last</dt><dd>{selPlace.last ?? "—"}</dd>
                  </dl>
                  <p class="note">{Object.entries(selPlace.types).map(([k, v]) => `${typeLabel(k)} ${v}`).join(", ")}</p>
                </div>
              )}
              <div class="pane" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
                <div class="head">
                  <div class="seg">
                    <button class={tab === "routes" ? "on" : ""} onClick={() => setQuery({ tab: null })}>Routes</button>
                    {place && <button class={tab === "activities" ? "on" : ""} onClick={() => setQuery({ tab: "activities" })}>Activities</button>}
                    <button class={tab === "places" ? "on" : ""} onClick={() => setQuery({ tab: "places" })}>Places</button>
                  </div>
                  <span class="count">{tab === "routes" ? `${routes.length}${selPlace ? ` starting at ${selPlace.name}` : ""}` : tab === "activities" ? (placeActs ? `${placeActs.n} with ${selPlace?.name ?? place} as main place` : "") : places.length}</span>
                </div>
                {tab === "routes"
                  ? <Table rows={routes} rowKey={(r) => r.id} cols={routeCols} onRow={(r) => setQuery({ route: r.id })} empty="No routes with ≥ 2 activities here." />
                  : tab === "activities"
                  ? <Table rows={placeActs?.activities ?? []} rowKey={(a) => a.id} cols={actCols} defaultSort={["start", -1]} onRow={(a) => navigate(`/activities/${a.id}`)}
                      empty={placeActs ? "No activities." : "Loading…"} />
                  : <Table rows={places} rowKey={(p) => p.id} cols={placeCols} selected={place} defaultSort={["n", -1]}
                      onRow={(p) => setQuery({ place: p.id, tab: null })} />}
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
