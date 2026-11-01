// A map showing encoded-polyline tracks (Activities list, Day map, Home),
// coloured by sport, with optional highlight + click, fitted to the tracks.
import type { Map as MLMap, MapLayerMouseEvent } from "maplibre-gl";
import { Marker } from "maplibre-gl";
import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { MapView, setData, fitBBox, EMPTY, type BasemapId } from "./MapView";
import { decodePolyline, unionBBox, bboxOfCoords, type BBox } from "../lib/geo";
import { sportColor } from "../lib/format";

interface MapTrack { id: string; sport: string; poly?: string | null; coords?: [number, number][]; color?: string; width?: number; muted?: boolean }

interface Props {
  tracks: MapTrack[];
  highlight?: string | null;
  onClick?: (id: string) => void;
  onHover?: (id: string | null) => void;
  class?: string;
  style?: any;
  /** fit to these instead of the tracks */
  fit?: BBox | null;
  markers?: boolean;
  defaultBasemap?: BasemapId;
  children?: ComponentChildren;
  empty?: string;
}

export function TracksMap({ tracks, highlight, onClick, onHover, class: cls, style, fit, markers = true, defaultBasemap, children, empty }: Props) {
  const [map, setMap] = useState<MLMap | null>(null);
  const cb = useRef({ onClick, onHover });
  cb.current = { onClick, onHover };
  const pins = useRef<Marker[]>([]);

  const decoded = useMemo(() => tracks.map((t) => ({ ...t, coords: t.coords ?? (t.poly ? decodePolyline(t.poly) : []) })), [tracks]);

  const onReady = (m: MLMap) => {
    for (const id of ["tracks", "hl"]) setData(m, id, EMPTY);
    m.addLayer({ id: "tracks-casing", type: "line", source: "tracks", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#ffffff", "line-width": ["+", ["get", "width"], 2.5], "line-opacity": ["case", ["get", "muted"], 0, 0.9] } });
    m.addLayer({ id: "tracks", type: "line", source: "tracks", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ["get", "width"], "line-opacity": ["case", ["get", "muted"], 0.35, 0.9] } });
    m.addLayer({ id: "hl", type: "line", source: "hl", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#1d2125", "line-width": 4.5, "line-opacity": 0.85 } });
    m.on("mousemove", "tracks", (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id;
      m.getCanvas().style.cursor = cb.current.onClick ? "pointer" : "";
      cb.current.onHover?.(id ?? null);
    });
    m.on("mouseleave", "tracks", () => { m.getCanvas().style.cursor = ""; cb.current.onHover?.(null); });
    m.on("click", "tracks", (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id;
      if (id) cb.current.onClick?.(String(id));
    });
    setMap(m);
  };

  useEffect(() => {
    if (!map) return;
    setData(map, "tracks", {
      type: "FeatureCollection",
      features: decoded.map((t) => ({ type: "Feature", properties: { id: t.id, color: t.color ?? sportColor(t.sport), width: t.width ?? 3, muted: !!t.muted },
        geometry: { type: "LineString", coordinates: t.coords } })),
    });
    pins.current.forEach((p) => p.remove());
    pins.current = [];
    if (markers && decoded.length <= 12) {
      for (const t of decoded) {
        if (t.muted || t.coords.length < 2) continue;
        const el = document.createElement("div");
        el.style.cssText = `width:10px;height:10px;border-radius:50%;background:${t.color ?? sportColor(t.sport)};border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.25)`;
        pins.current.push(new Marker({ element: el }).setLngLat(t.coords[0]).addTo(map));
      }
    }
    const b = fit ?? unionBBox(decoded.filter((t) => !t.muted || decoded.every((x) => x.muted)).map((t) => bboxOfCoords(t.coords)).filter((x): x is BBox => !!x));
    fitBBox(map, b, { padding: 40 });
  }, [map, decoded, fit]);

  useEffect(() => {
    if (!map) return;
    const t = decoded.find((x) => x.id === highlight);
    setData(map, "hl", t ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: t.coords } } : EMPTY);
  }, [map, highlight, decoded]);

  return (
    <MapView class={cls} style={style} onReady={onReady} defaultBasemap={defaultBasemap}>
      {!tracks.length && <div class="msg">{empty ?? "No GPS track"}</div>}
      {children}
    </MapView>
  );
}
