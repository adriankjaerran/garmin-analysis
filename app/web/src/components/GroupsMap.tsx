// Map for the groups browser: place points (size = sessions), place / region shapes (circle or polygon),
// route lines and member tracks. Hover / click report the group key.
import type { Map as MLMap, MapLayerMouseEvent } from "maplibre-gl";
import { useEffect, useMemo, useState } from "preact/hooks";
import { MapView, setData, fitBBox, EMPTY } from "./MapView";
import { bboxOfCoords, decodePolyline, unionBBox, type BBox } from "../lib/geo";
import { sportColor } from "../lib/format";

export interface GPoint { key: string; lat: number; lon: number; n: number; color?: string }
export interface GShape { key: string; circle?: [number, number, number]; poly?: [number, number][] }
export interface GLine { id: string; key: string; sport: string; poly?: string | null; coords?: [number, number][] }

interface Props {
  points?: GPoint[]; shapes?: GShape[]; lines?: GLine[];
  /** tracks drawn on top for the hovered / selected group */
  members?: GLine[];
  highlight?: string | null; selected?: string | null;
  onHover?: (key: string | null) => void; onClick?: (key: string) => void;
  fit?: BBox | null; style?: any; empty?: string;
}

function ring(s: GShape): [number, number][] {
  if (s.poly) { const r = s.poly.map(([la, lo]) => [lo, la] as [number, number]); return [...r, r[0]]; }
  const [la, lo, m] = s.circle!;
  const out: [number, number][] = [];
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * 2 * Math.PI;
    out.push([lo + (m / (111320 * Math.cos((la * Math.PI) / 180))) * Math.cos(a), la + (m / 110540) * Math.sin(a)]);
  }
  return out;
}

export function GroupsMap({ points = [], shapes = [], lines = [], members = [], highlight, selected, onHover, onClick, fit, style, empty }: Props) {
  const [map, setMap] = useState<MLMap | null>(null);
  const cb = useMemo(() => ({ onHover, onClick }), []);
  cb.onHover = onHover; cb.onClick = onClick;

  const L = useMemo(() => lines.map((l) => ({ ...l, coords: l.coords ?? (l.poly ? decodePolyline(l.poly) : []) })), [lines]);
  const M = useMemo(() => members.map((l) => ({ ...l, coords: l.coords ?? (l.poly ? decodePolyline(l.poly) : []) })), [members]);
  const S = useMemo(() => shapes.map((s) => ({ key: s.key, ring: ring(s) })), [shapes]);

  const onReady = (m: MLMap) => {
    for (const id of ["shapes", "lines", "members", "pts", "hl"]) setData(m, id, EMPTY);
    m.addLayer({ id: "shapes-fill", type: "fill", source: "shapes", paint: { "fill-color": "#2f5fd0", "fill-opacity": ["case", ["get", "on"], 0.22, 0.07] } });
    m.addLayer({ id: "shapes-line", type: "line", source: "shapes", paint: { "line-color": "#2f5fd0", "line-width": ["case", ["get", "on"], 2.5, 1.2], "line-opacity": 0.8 } });
    m.addLayer({ id: "lines", type: "line", source: "lines", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 2.5, "line-opacity": 0.6 } });
    m.addLayer({ id: "members", type: "line", source: "members", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 2, "line-opacity": 0.75 } });
    m.addLayer({ id: "hl", type: "line", source: "hl", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#1d2125", "line-width": 4.5, "line-opacity": 0.85 } });
    m.addLayer({ id: "pts", type: "circle", source: "pts", paint: {
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 4, 10, 16],
      "circle-color": ["get", "color"], "circle-opacity": ["case", ["get", "on"], 0.95, 0.6],
      "circle-stroke-color": ["case", ["get", "on"], "#1d2125", "#fff"], "circle-stroke-width": ["case", ["get", "on"], 2.5, 1.5] } });
    for (const layer of ["pts", "lines", "shapes-fill"]) {
      m.on("mousemove", layer, (e: MapLayerMouseEvent) => {
        // points win over lines over shapes
        const top = m.queryRenderedFeatures(e.point, { layers: ["pts", "lines", "shapes-fill"] })[0];
        const k = top?.properties?.key;
        m.getCanvas().style.cursor = k ? "pointer" : "";
        cb.onHover?.(k ?? null);
      });
      m.on("mouseleave", layer, () => { m.getCanvas().style.cursor = ""; cb.onHover?.(null); });
    }
    m.on("click", (e) => {
      const top = m.queryRenderedFeatures(e.point, { layers: ["pts", "lines", "shapes-fill"] })[0];
      if (top?.properties?.key) cb.onClick?.(String(top.properties.key));
    });
    setMap(m);
  };

  const on = (k: string) => k === highlight || k === selected;

  useEffect(() => {
    if (!map) return;
    setData(map, "shapes", { type: "FeatureCollection", features: S.map((s) => ({ type: "Feature", properties: { key: s.key, on: on(s.key) },
      geometry: { type: "Polygon", coordinates: [s.ring] } })) });
    setData(map, "pts", { type: "FeatureCollection", features: points.map((p) => ({ type: "Feature",
      properties: { key: p.key, n: p.n, color: p.color ?? "#2f5fd0", on: on(p.key) }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } })) });
    setData(map, "lines", { type: "FeatureCollection", features: L.map((l) => ({ type: "Feature", properties: { key: l.key, color: sportColor(l.sport) },
      geometry: { type: "LineString", coordinates: l.coords } })) });
    const hl = L.filter((l) => on(l.key));
    setData(map, "hl", { type: "FeatureCollection", features: hl.map((l) => ({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: l.coords } })) });
  }, [map, S, points, L, highlight, selected]);

  useEffect(() => {
    if (!map) return;
    setData(map, "members", { type: "FeatureCollection", features: M.filter((l) => l.coords.length > 1).map((l) => ({ type: "Feature",
      properties: { key: l.key, color: sportColor(l.sport) }, geometry: { type: "LineString", coordinates: l.coords } })) });
  }, [map, M]);

  useEffect(() => {
    if (!map) return;
    const b = fit ?? unionBBox([
      ...points.map((p) => [p.lon, p.lat, p.lon, p.lat] as BBox),
      ...S.map((s) => bboxOfCoords(s.ring)), ...L.map((l) => bboxOfCoords(l.coords)),
    ].filter((x): x is BBox => !!x));
    if (b) fitBBox(map, b, { padding: 40, maxZoom: 15 });
  }, [map, fit, points.length, S.length, L.length]);

  const nothing = !points.length && !shapes.length && !lines.length && !members.length;
  return (
    <MapView style={style} onReady={onReady}>
      {nothing && <div class="msg">{empty ?? "Nothing to show on the map"}</div>}
    </MapView>
  );
}
