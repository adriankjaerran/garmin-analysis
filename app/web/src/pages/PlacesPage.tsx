// Places (start-point clusters) and routes (repeated tracks) from groups.json, on a map.
// ?place=<id> filters routes to that place, ?route=R3 shows the route: reference line, member tracks, sessions.
import type { Map as MLMap, MapLayerMouseEvent } from "maplibre-gl";
import { Popup } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { qs, useApi, type Place, type RouteDetail, type RouteRow } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { SPORT_LABEL, dur, km, sportColor, stypeLabel, typeLabel } from "../lib/format";
import { decodePolyline, type BBox } from "../lib/geo";
import { MapView, setData, fitBBox, EMPTY } from "../components/MapView";
import { Table, type Col } from "../components/Table";
import { SportDot, speedText } from "../components/ActivityBits";

const SHAPE: Record<string, string> = { laps: "laps", out_and_back: "out & back", point_to_point: "point to point", loop_cw: "loop ↻", loop_ccw: "loop ↺" };
const famOf = (types: Record<string, number>) => {
  const top = Object.entries(types).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "other";
  return top.includes("ski") ? "ski" : top.includes("run") ? "run" : top === "hiking" || top === "walking" ? "hike" : top === "cycling" ? "bike" : top.includes("swim") ? "swim" : "other";
};

const routeCols: Col<RouteRow>[] = [
  { key: "id", label: "Route", sort: (r) => Number(r.id.slice(1)) },
  { key: "name", label: "Name", render: (r) => <span><SportDot sport={r.sport} />{r.name}</span> },
  { key: "dist_km", label: "km", num: true, render: (r) => km(r.dist_km) },
  { key: "n", label: "Sessions", num: true },
  { key: "last", label: "Last", render: (r) => r.last ?? "—" },
];
const placeCols: Col<Place & { fam: string }>[] = [
  { key: "name", label: "Place", render: (p) => <span><SportDot sport={p.fam} />{p.name}</span> },
  { key: "n", label: "Sessions", num: true },
  { key: "routes", label: "Routes", num: true },
  { key: "area", label: "Area", render: (p) => p.area ?? "—" },
  { key: "last", label: "Last", render: (p) => p.last ?? "—" },
];

export function PlacesPage() {
  const { query } = useLocation();
  const place = query.get("place"), route = query.get("route"), sport = query.get("sport") || "";
  const tab = query.get("tab") === "places" ? "places" : "routes";
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
      popup.setLngLat([p.lon, p.lat]).setHTML(`<b>${p.name}</b><br>${p.n} sessions, ${p.routes} routes<br>${Object.entries(p.types).map(([k, v]) => `${typeLabel(k)} ${v}`).join(", ")}`).addTo(m);
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
  return (
    <main class="full">
      <div class="toolbar">
        <h1>Places & routes</h1>
        <select value={sport} onChange={(e) => setQuery({ sport: (e.target as HTMLSelectElement).value })}>
          <option value="">All sports</option>
          {["run", "ski", "hike", "bike"].map((s) => <option key={s} value={s}>{SPORT_LABEL[s]}</option>)}
        </select>
        <span class="count">{places.length} places, {rs?.routes.length ?? 0} routes with ≥ 2 sessions</span>
        <div class="tools">
          {(place || route) && <button onClick={() => setQuery({ place: null, route: null })}>Show all</button>}
        </div>
      </div>
      <div class="split">
        <div class="left"><MapView style={{ flex: 1 }} onReady={onReady} /></div>
        <div class="right" style={{ width: "560px" }}>
          {r ? (
            <>
              <div class="pane">
                <div class="head">
                  <h2>{r.id} {r.name}</h2>
                  <div class="tools"><a href={`/places?place=${encodeURIComponent(r.place)}`}>Back to {r.place}</a></div>
                </div>
                <dl class="kv">
                  <dt>Place</dt><dd>{r.place}</dd>
                  <dt>Sport</dt><dd><SportDot sport={r.sport} />{SPORT_LABEL[r.sport] ?? r.sport}</dd>
                  <dt>Shape</dt><dd>{SHAPE[r.shape] ?? r.shape}</dd>
                  <dt>Distance</dt><dd>{km(r.dist_km)} km</dd>
                  <dt>Sessions</dt><dd>{r.n}</dd>
                  <dt>First / last</dt><dd>{r.first} / {r.last}</dd>
                </dl>
                <p class="note">Dashed = reference session{r.waypoints?.length ? ", circles = common stops" : ""}. Leg-by-leg comparison: Compare (soon) and the report's route view.</p>
              </div>
              <div class="pane" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
                <div class="head"><h2>Sessions</h2><span class="count">{r.members.length}</span></div>
                <Table rows={r.members} rowKey={(a) => a.id} highlight={hlMember} onHover={(a) => setHlMember(a?.id ?? null)}
                  onRow={(a) => navigate(`/activities/${a.id}`)} defaultSort={["date", -1]} cols={[
                    { key: "date", label: "Date" },
                    { key: "stype", label: "Type", render: (a) => stypeLabel(a.stype) || a.type },
                    { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s) },
                    { key: "pause_s", label: "Paused", num: true, render: (a) => dur(a.pause_s) },
                    { key: "speed", label: "Pace / speed", num: true, render: speedText, sort: (a) => (a.dist_km && a.moving_s ? a.dist_km / a.moving_s : null) },
                    { key: "hr", label: "HR", num: true, render: (a) => a.hr ?? "—" },
                    { key: "route_dir", label: "Dir", render: (a) => a.route_dir ?? "—" },
                  ]} />
              </div>
            </>
          ) : (
            <>
              {selPlace && (
                <div class="pane">
                  <div class="head"><h2><SportDot sport={selPlace.fam} />{selPlace.name}</h2>
                    <div class="tools"><a href={`/activities?q=${encodeURIComponent(selPlace.name)}`}>Activities</a></div></div>
                  <dl class="kv">
                    <dt>Sessions</dt><dd>{selPlace.n}</dd>
                    <dt>Routes</dt><dd>{selPlace.routes}</dd>
                    <dt>Area</dt><dd>{selPlace.area ?? "—"}</dd>
                    <dt>Last</dt><dd>{selPlace.last ?? "—"}</dd>
                  </dl>
                  <p class="note">{Object.entries(selPlace.types).map(([k, v]) => `${typeLabel(k)} ${v}`).join(", ")}</p>
                </div>
              )}
              <div class="pane" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
                <div class="head">
                  <div class="seg">
                    <button class={tab === "routes" ? "on" : ""} onClick={() => setQuery({ tab: null })}>Routes</button>
                    <button class={tab === "places" ? "on" : ""} onClick={() => setQuery({ tab: "places" })}>Places</button>
                  </div>
                  <span class="count">{tab === "routes" ? `${routes.length}${place ? ` at ${place}` : ""}` : places.length}</span>
                </div>
                {tab === "routes"
                  ? <Table rows={routes} rowKey={(r) => r.id} cols={routeCols} onRow={(r) => setQuery({ route: r.id })} empty="No routes with ≥ 2 sessions here." />
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
