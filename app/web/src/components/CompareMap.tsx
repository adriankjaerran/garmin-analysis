// Map for the Compare page: every session's track (reference dark, others coloured or grey), the route
// reference (dashed) and common stops, and one marker per session at the chart cursor (ghost race). With a common
// start line, each track's lead-in (before its start on the line, `cut` = file elapsed s) is drawn thin and dashed;
// with one rep selected (`win`) only that rep's stretch is drawn fully and the map is fitted to it.
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
  /** common start: per session the file elapsed second where it starts on the line (lead-in before it dimmed) */
  cut?: Record<string, number>;
  /** one rep / break selected: per session the [from, to] file elapsed seconds drawn (the rest dimmed), map fitted to them */
  win?: Record<string, [number, number]> | null;
}

export function CompareMap({ tracks, order, colors, refId, focus, many, positions, routeRef, onPick, onClick, cut, win }: Props) {
  const [map, setMap] = useState<MLMap | null>(null);
  const cb = useMemo(() => ({ onPick, onClick }), []);
  cb.onPick = onPick; cb.onClick = onClick;

  // coords = the compared part of each track (from its start on the line with a common start, or the selected rep's
  // window); lead = the parts outside it (drawn dimmed)
  const { coords, lead } = useMemo(() => {
    const o: Record<string, [number, number][]> = {}, l: Record<string, [number, number][][]> = {};
    for (const id of order) {
      const t = tracks[id];
      if (!t) continue;
      const [t0, t1] = win ? win[id] ?? [Infinity, Infinity] : [cut?.[id] ?? -Infinity, Infinity];
      const c: [number, number][] = [], parts: [number, number][][] = [[]];
      for (let i = 0; i < t.t.length; i++) {
        if (t.lat[i] === null) continue;
        const pt: [number, number] = [t.lon[i], t.lat[i]];
        if (t.t[i] < t0 || t.t[i] > t1) {
          if (c.length && !parts[parts.length - 1].length) parts[parts.length - 1].push(c[c.length - 1]);   // joins the drawn part
          parts[parts.length - 1].push(pt);
        } else {
          if (parts[parts.length - 1].length) { parts[parts.length - 1].push(pt); parts.push([]); }
          c.push(pt);
        }
      }
      o[id] = c;
      const lp = parts.filter((p) => p.length > 1);
      if (lp.length) l[id] = lp;
    }
    return { coords: o, lead: l };
  }, [tracks, order, cut, win]);

  const onReady = (m: MLMap) => {
    for (const s of ["c-tracks", "c-lead", "c-ref", "c-wp", "c-focus", "c-pos"]) setData(m, s, EMPTY);
    m.addLayer({ id: "c-ref", type: "line", source: "c-ref", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#1d2125", "line-width": 2, "line-dasharray": [2, 2], "line-opacity": 0.6 } });
    m.addLayer({ id: "c-lead", type: "line", source: "c-lead", layout: { "line-join": "round", "line-cap": "butt" },
      paint: { "line-color": ["get", "color"], "line-width": 1.5, "line-dasharray": [1.5, 2], "line-opacity": 0.45 } });
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
    setData(map, "c-lead", { type: "FeatureCollection", features: ids.filter((id) => lead[id]).map((id) => ({
      type: "Feature", properties: { id, color: colors[id] }, geometry: { type: "MultiLineString", coordinates: lead[id] } })) });
    const b = unionBBox(Object.values(coords).map((c) => bboxOfCoords(c)).filter((x): x is BBox => !!x));
    fitBBox(map, b, { padding: 30 });
  }, [map, coords, lead, colors, refId, many]);

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

  return <MapView onReady={onReady} class="bordered" defaultBasemap="grey">{!Object.keys(tracks).length && <div class="msg">No GPS tracks</div>}</MapView>;
}
