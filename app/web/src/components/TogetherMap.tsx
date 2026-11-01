// Map for the Together session page: one line per person in their colour (light variant: basemaps are light),
// and one dot per person with their initials (HTML markers: the raster styles have no glyphs) at the chart
// cursor or the replay clock. Refits when `fitKey` changes.
import { Marker, type Map as MLMap } from "maplibre-gl";
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { MapView, setData, fitBBox, EMPTY } from "./MapView";
import { bboxOfCoords, unionBBox, type BBox } from "../lib/geo";

export interface MapLine { key: string; colour: string; coords: [number, number][]; width?: number; opacity?: number; faint?: boolean }

const STACK_PX = 2.5;   // overlapping tracks (side by side): each line under the next is this much wider, so all stay visible
export interface MapDot { key: string; colour: string; initials: string; pos: [number, number] | null; title?: string; strong?: boolean }

interface Props {
  lines: MapLine[];
  dots: MapDot[];
  fitKey: string;
  /** fit to these points (default: the strong lines) */
  fitTo?: [number, number][] | null;
  children?: ComponentChildren;
}

export function TogetherMap({ lines, dots, fitKey, fitTo, children }: Props) {
  const [map, setMap] = useState<MLMap | null>(null);
  const markers = useRef<Record<string, { m: Marker; el: HTMLDivElement; on: boolean }>>({});

  const onReady = (m: MLMap) => {
    setData(m, "tg-faint", EMPTY);
    setData(m, "tg-lines", EMPTY);
    m.addLayer({ id: "tg-faint", type: "line", source: "tg-faint", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "colour"], "line-width": ["get", "width"], "line-opacity": ["get", "op"] } });
    m.addLayer({ id: "tg-casing", type: "line", source: "tg-lines", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#fff", "line-width": ["+", ["get", "width"], 2.5], "line-opacity": 0.8 } });
    m.addLayer({ id: "tg-lines", type: "line", source: "tg-lines", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "colour"], "line-width": ["get", "width"], "line-opacity": ["get", "op"] } });
    setMap(m);
  };

  useEffect(() => {
    if (!map) return;
    // drawn in order (the last on top); the lines below are wider, so two people on the same path show as a
    // centre line with the other's colour along both edges
    const fc = (faint: boolean): GeoJSON.FeatureCollection => {
      const ls = lines.filter((l) => !!l.faint === faint && l.coords.length > 1);
      return { type: "FeatureCollection", features: ls.map((l, i) => ({
        type: "Feature", properties: { key: l.key, colour: l.colour, width: (l.width ?? (faint ? 1.5 : 3)) + (ls.length - 1 - i) * (faint ? 2 : STACK_PX),
          op: l.opacity ?? (faint ? 0.45 : 0.95) },
        geometry: { type: "LineString", coordinates: l.coords } })) };
    };
    setData(map, "tg-faint", fc(true));
    setData(map, "tg-lines", fc(false));
  }, [map, lines]);

  useEffect(() => {
    if (!map) return;
    const strong = lines.filter((l) => !l.faint && l.coords.length > 1);
    const use = strong.length ? strong : lines;
    const b = fitTo?.length ? bboxOfCoords(fitTo) : unionBBox(use.map((l) => bboxOfCoords(l.coords)).filter((x): x is BBox => !!x));
    fitBBox(map, b, { padding: 36, maxZoom: 17 });
  }, [map, fitKey]);

  useEffect(() => {
    if (!map) return;
    const have = markers.current;
    for (const d of dots) {
      let mk = have[d.key];
      if (!mk) {
        const el = document.createElement("div");
        el.className = "tg-dot";
        mk = have[d.key] = { m: new Marker({ element: el }), el, on: false };
      }
      mk.el.textContent = d.initials;
      mk.el.style.background = d.colour;
      mk.el.title = d.title ?? "";
      mk.el.classList.toggle("strong", !!d.strong);
      if (d.pos) {
        mk.m.setLngLat(d.pos);
        if (!mk.on) { mk.m.addTo(map); mk.on = true; }
      } else if (mk.on) { mk.m.remove(); mk.on = false; }
    }
    for (const k of Object.keys(have)) if (!dots.some((d) => d.key === k)) { have[k].m.remove(); delete have[k]; }
  }, [map, dots]);

  useEffect(() => () => { for (const k of Object.keys(markers.current)) markers.current[k].m.remove(); markers.current = {}; }, []);

  return <MapView onReady={onReady} class="bordered" defaultBasemap="grey">{children}</MapView>;
}
