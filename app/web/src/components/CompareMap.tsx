// Map for the Compare page: every session's track (reference dark, others coloured or grey), the route
// reference (dashed) and common stops, and one marker per session at the chart cursor (ghost race).
import type { Map as MLMap, MapLayerMouseEvent } from "maplibre-gl";
import { useEffect, useMemo, useState } from "preact/hooks";
import { MapView, setData, fitBBox, EMPTY } from "./MapView";
import { unionBBox, bboxOfCoords, type BBox } from "../lib/geo";
import { FOCUS_MANY, type Track } from "../lib/compare";

interface Props {
  tracks: Record<string, Track>;
  order: string[];
  colors: Record<string, string>;
  refId: string | null;
  focus: string | null;
  many: boolean;
  positions: Record<string, [number, number] | null>;
  routeRef?: { lat: number[]; lon: number[]; waypoints?: { lat: number; lon: number; name: string }[] } | null;
  onPick?: (id: string | null) => void;
  onClick?: (id: string) => void;
}

export function CompareMap({ tracks, order, colors, refId, focus, many, positions, routeRef, onPick, onClick }: Props) {
  const [map, setMap] = useState<MLMap | null>(null);
  const cb = useMemo(() => ({ onPick, onClick }), []);
  cb.onPick = onPick; cb.onClick = onClick;

  const coords = useMemo(() => {
    const o: Record<string, [number, number][]> = {};
    for (const id of order) {
      const t = tracks[id];
      if (!t) continue;
      const c: [number, number][] = [];
      for (let i = 0; i < t.t.length; i++) if (t.lat[i] !== null) c.push([t.lon[i], t.lat[i]]);
      o[id] = c;
    }
    return o;
  }, [tracks, order]);

  const onReady = (m: MLMap) => {
    for (const s of ["c-tracks", "c-ref", "c-wp", "c-focus", "c-pos"]) setData(m, s, EMPTY);
    m.addLayer({ id: "c-ref", type: "line", source: "c-ref", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#1d2125", "line-width": 2, "line-dasharray": [2, 2], "line-opacity": 0.6 } });
    m.addLayer({ id: "c-tracks", type: "line", source: "c-tracks", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ["get", "width"], "line-opacity": ["get", "op"] } });
    m.addLayer({ id: "c-focus-casing", type: "line", source: "c-focus", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#fff", "line-width": 7 } });
    m.addLayer({ id: "c-focus", type: "line", source: "c-focus", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 4 } });
    m.addLayer({ id: "c-wp", type: "circle", source: "c-wp", paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#67707a", "circle-stroke-width": 1.5 } });
    m.addLayer({ id: "c-pos", type: "circle", source: "c-pos", paint: {
      "circle-radius": ["get", "r"], "circle-color": ["get", "color"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    m.on("mousemove", "c-tracks", (e: MapLayerMouseEvent) => {
      m.getCanvas().style.cursor = "pointer";
      cb.onPick?.(String(e.features?.[0]?.properties?.id ?? "") || null);
    });
    m.on("mouseleave", "c-tracks", () => { m.getCanvas().style.cursor = ""; cb.onPick?.(null); });
    m.on("click", "c-tracks", (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id;
      if (id) cb.onClick?.(String(id));
    });
    setMap(m);
  };

  // tracks + fit (reference drawn last = on top)
  useEffect(() => {
    if (!map) return;
    const ids = [...order.filter((i) => i !== refId), ...order.filter((i) => i === refId)];
    setData(map, "c-tracks", { type: "FeatureCollection", features: ids.filter((id) => coords[id]?.length > 1).map((id) => ({
      type: "Feature", properties: { id, color: colors[id], width: id === refId ? 3.5 : many ? 2 : 3, op: id === refId ? 0.95 : many ? 0.6 : 0.85 },
      geometry: { type: "LineString", coordinates: coords[id] } })) });
    const b = unionBBox(Object.values(coords).map((c) => bboxOfCoords(c)).filter((x): x is BBox => !!x));
    fitBBox(map, b, { padding: 30 });
  }, [map, coords, colors, refId, many]);

  useEffect(() => {
    if (!map) return;
    setData(map, "c-ref", routeRef ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: routeRef.lon.map((x, i) => [x, routeRef.lat[i]]) } } : EMPTY);
    setData(map, "c-wp", routeRef?.waypoints ? { type: "FeatureCollection", features: routeRef.waypoints.map((w) => ({
      type: "Feature", properties: { name: w.name }, geometry: { type: "Point", coordinates: [w.lon, w.lat] } })) } : EMPTY);
  }, [map, routeRef]);

  useEffect(() => {
    if (!map) return;
    const c = focus ? coords[focus] : null;
    setData(map, "c-focus", c && c.length > 1 ? { type: "Feature", properties: { color: many && focus !== refId ? FOCUS_MANY : colors[focus!] },
      geometry: { type: "LineString", coordinates: c } } : EMPTY);
  }, [map, focus, coords, colors, many]);

  useEffect(() => {
    if (!map) return;
    // focus and reference markers on top
    const ids = Object.keys(positions).sort((a, b) => (a === focus ? 2 : a === refId ? 1 : 0) - (b === focus ? 2 : b === refId ? 1 : 0));
    setData(map, "c-pos", { type: "FeatureCollection", features: ids.filter((id) => positions[id]).map((id) => ({
      type: "Feature", properties: { id, r: id === focus || id === refId ? 7 : many ? 4.5 : 6,
        color: id === focus && many && id !== refId ? FOCUS_MANY : many && id !== refId && id !== focus ? "#8a9199" : colors[id] },
      geometry: { type: "Point", coordinates: positions[id]! } })) });
  }, [map, positions, focus, colors, many]);

  return <MapView onReady={onReady} class="bordered">{!Object.keys(tracks).length && <div class="msg">No GPS tracks</div>}</MapView>;
}
