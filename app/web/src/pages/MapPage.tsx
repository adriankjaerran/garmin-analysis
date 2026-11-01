// Overview map of every GPS track; filter by sport / year; hover shows the activity, click opens it.
import type { Map as MLMap, MapLayerMouseEvent } from "maplibre-gl";
import { Popup } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { MapView, setData, fitBBox, EMPTY } from "../components/MapView";
import { qs, useApi, type Meta, type TrackLite } from "../lib/api";
import { decodePolyline, type BBox } from "../lib/geo";
import { SPORT_LABEL, km, sportColor, typeLabel } from "../lib/format";
import { navigate, setQuery, useLocation } from "../lib/router";
import { PageHeader } from "../components/PageHeader";

/** Fit to where most tracks are: tracks whose centre is within ~60 km of the median centre. */
function homeBBox(tracks: TrackLite[]): BBox | null {
  if (!tracks.length) return null;
  const cx = tracks.map((t) => (t.bbox[0] + t.bbox[2]) / 2).sort((a, b) => a - b);
  const cy = tracks.map((t) => (t.bbox[1] + t.bbox[3]) / 2).sort((a, b) => a - b);
  const mx = cx[cx.length >> 1], my = cy[cy.length >> 1];
  const near = tracks.filter((t) => Math.abs((t.bbox[0] + t.bbox[2]) / 2 - mx) < 1.0 && Math.abs((t.bbox[1] + t.bbox[3]) / 2 - my) < 0.5);
  const use = near.length ? near : tracks;
  const xs = use.flatMap((t) => [t.bbox[0], t.bbox[2]]).sort((a, b) => a - b);
  const ys = use.flatMap((t) => [t.bbox[1], t.bbox[3]]).sort((a, b) => a - b);
  const q = (a: number[], p: number) => a[Math.floor(p * (a.length - 1))];
  return [q(xs, 0.03), q(ys, 0.03), q(xs, 0.97), q(ys, 0.97)];
}

export function MapPage() {
  const { query } = useLocation();
  const sport = query.get("sport") || "";
  const year = query.get("year") || "";
  const mode = query.get("mode") === "heat" ? "heat" : "lines";
  const meta = useApi<Meta>("/api/meta").data;
  const url = "/api/map/tracks" + qs({ sport, from: year && `${year}-01-01`, to: year && `${year}-12-31` });
  const { data, loading, error } = useApi<{ n: number; tracks: TrackLite[] }>(url);
  const [map, setMap] = useState<MLMap | null>(null);
  const fitted = useRef(false);
  const tracksRef = useRef<TrackLite[]>([]);

  const fc = useMemo<GeoJSON.FeatureCollection>(() => {
    if (!data) return EMPTY;
    return {
      type: "FeatureCollection",
      features: data.tracks.map((t, i) => ({
        type: "Feature", id: i,
        properties: { id: t.id, sport: t.sport, color: sportColor(t.sport) },
        geometry: { type: "LineString", coordinates: decodePolyline(t.poly) },
      })),
    };
  }, [data]);

  const onReady = (m: MLMap) => {
    setData(m, "tracks", EMPTY);
    setData(m, "hover", EMPTY);
    m.addLayer({ id: "tracks", type: "line", source: "tracks", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.2, 14, 2.5], "line-opacity": 0.65 } });
    m.addLayer({ id: "hover", type: "line", source: "hover", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#1d2125", "line-width": 4, "line-opacity": 0.9 } });
    const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 10, maxWidth: "320px" });
    m.on("mousemove", "tracks", (e: MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      m.getCanvas().style.cursor = "pointer";
      const t = tracksRef.current[f.id as number];
      if (!t) return;
      setData(m, "hover", { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: decodePolyline(t.poly) } });
      popup.setLngLat(e.lngLat).setHTML(
        `<b>${t.date}</b> ${SPORT_LABEL[t.sport]} · ${typeLabel(t.type || "")}<br>${t.name ?? ""} · ${km(t.km)} km<br><span style="color:var(--text-muted)">Click to open</span>`).addTo(m);
    });
    m.on("mouseleave", "tracks", () => { m.getCanvas().style.cursor = ""; popup.remove(); setData(m, "hover", EMPTY); });
    m.on("click", "tracks", (e: MapLayerMouseEvent) => {
      const t = tracksRef.current[e.features?.[0]?.id as number];
      if (t) navigate(`/activities/${t.id}`);
    });
    m.on("moveend", () => {
      const c = m.getCenter();
      setQuery({ v: `${c.lng.toFixed(4)},${c.lat.toFixed(4)},${m.getZoom().toFixed(2)}` });
    });
    const v = new URLSearchParams(location.search).get("v");
    if (v) {
      const [lng, lat, z] = v.split(",").map(Number);
      if (!isNaN(z)) { m.jumpTo({ center: [lng, lat], zoom: z }); fitted.current = true; }
    }
    setMap(m);
  };

  useEffect(() => {
    if (!map || !data) return;
    tracksRef.current = data.tracks;
    setData(map, "tracks", fc);
    if (!fitted.current && data.tracks.length) { fitBBox(map, homeBBox(data.tracks), { padding: 30 }); fitted.current = true; }
  }, [map, fc]);

  useEffect(() => {
    if (!map) return;
    if (mode === "heat") {
      map.setPaintProperty("tracks", "line-color", "#d7301f");
      map.setPaintProperty("tracks", "line-opacity", 0.18);
    } else {
      map.setPaintProperty("tracks", "line-color", ["get", "color"]);
      map.setPaintProperty("tracks", "line-opacity", 0.65);
    }
  }, [map, mode]);

  return (
    <main class="full">
      <PageHeader class="toolbar" title="Map" sub={loading ? "Loading…" : error ? "" : `${data?.n ?? 0} tracks`}
        about="Every GPS track you have recorded, coloured by sport or as a heat map of where you go most."
        tips={["Hover a track to see the activity; click it to open", "Heat shows where tracks overlap most", "The view (centre, zoom) is in the URL: share or bookmark it"]}>
        <div class="chips">
          <button class={"chip" + (!sport ? " on" : "")} onClick={() => setQuery({ sport: null })}>All</button>
          {meta?.sports.map((s) => (
            <button key={s.id} class={"chip" + (sport === s.id ? " on" : "")} onClick={() => setQuery({ sport: sport === s.id ? null : s.id })}>
              <span class="dot" style={{ background: sportColor(s.id) }} />{SPORT_LABEL[s.id]}
            </button>
          ))}
        </div>
        <select value={year} onChange={(e) => setQuery({ year: (e.target as HTMLSelectElement).value })}>
          <option value="">All years</option>
          {meta?.years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <button onClick={() => map && data && fitBBox(map, homeBBox(data.tracks), { padding: 30, animate: true })}>Fit home</button>
        <div class="seg">
          <button class={mode === "lines" ? "on" : ""} onClick={() => setQuery({ mode: null })}>By sport</button>
          <button class={mode === "heat" ? "on" : ""} onClick={() => setQuery({ mode: "heat" })}>Heat</button>
        </div>
      </PageHeader>
      {error && <div class="err pane">Could not load the tracks: {error}</div>}
      <MapView class="fill" style={{ flex: 1 }} onReady={onReady} defaultBasemap="grey" />
    </main>
  );
}
