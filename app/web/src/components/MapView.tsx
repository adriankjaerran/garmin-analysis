// MapLibre wrapper: raster basemap (Kartverket topo / grey, OpenStreetMap, OpenTopoMap) with a toggle that
// is shared by every map in the app (localStorage), plus small helpers for GeoJSON sources.
import { Map as MLMap, NavigationControl, ScaleControl, AttributionControl, LngLatBounds, setWorkerUrl } from "maplibre-gl";
// MapLibre 6 loads its worker as a separate module next to the bundle; let Vite emit it and point at it
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { GeoJSONSource, LayerSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { BBox } from "../lib/geo";

setWorkerUrl(workerUrl);

const KV = (layer: string) => [`https://cache.kartverket.no/v1/wmts/1.0.0/${layer}/default/webmercator/{z}/{y}/{x}.png`];
const KV_ATTR = '© <a href="https://www.kartverket.no/" target="_blank">Kartverket</a>';
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors';

export const BASEMAPS = {
  topo: { label: "Topo", tiles: KV("topo"), attribution: KV_ATTR, maxzoom: 18 },
  grey: { label: "Grey", tiles: KV("topograatone"), attribution: KV_ATTR, maxzoom: 18 },
  osm: { label: "OSM", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], attribution: OSM_ATTR, maxzoom: 19 },
  otm: {
    label: "OpenTopo", tiles: ["a", "b", "c"].map((s) => `https://${s}.tile.opentopomap.org/{z}/{x}/{y}.png`),
    attribution: `${OSM_ATTR}, SRTM | © <a href="https://opentopomap.org" target="_blank">OpenTopoMap</a> (CC-BY-SA)`, maxzoom: 17,
  },
} as const;
export type BasemapId = keyof typeof BASEMAPS;

const KEY = "garmin.basemap";
const EVT = "app:basemap";
export const getBasemap = (fallback: BasemapId = "topo"): BasemapId => {
  const v = localStorage.getItem(KEY) as BasemapId | null;
  return v && v in BASEMAPS ? v : fallback;
};
function setBasemap(id: BasemapId) {
  localStorage.setItem(KEY, id);
  window.dispatchEvent(new CustomEvent(EVT, { detail: id }));
}

function baseSource(id: BasemapId) {
  const b = BASEMAPS[id];
  return { type: "raster" as const, tiles: [...b.tiles], tileSize: 256, maxzoom: b.maxzoom, attribution: b.attribution };
}

function applyBasemap(map: MLMap, id: BasemapId) {
  const layers = map.getStyle().layers.map((l) => l.id).filter((l) => l !== "base");
  if (map.getLayer("base")) map.removeLayer("base");
  if (map.getSource("base")) map.removeSource("base");
  map.addSource("base", baseSource(id));
  map.addLayer({ id: "base", type: "raster", source: "base" }, layers[0]);
}

interface Props {
  class?: string;
  style?: string | Record<string, string | number>;
  /** called once when the style is loaded; add sources/layers/handlers here */
  onReady?: (map: MLMap) => void;
  /** initial view when no bbox is known */
  bbox?: BBox | null;
  interactive?: boolean;
  /** per-map default basemap; the user's toggle (stored) wins */
  defaultBasemap?: BasemapId;
  children?: ComponentChildren;
  compact?: boolean;
}

export function MapView({ class: cls = "", style, onReady, bbox, interactive = true, defaultBasemap = "topo", children, compact }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const [bm, setBm] = useState<BasemapId>(() => getBasemap(defaultBasemap));
  const mapRef = useRef<MLMap | null>(null);

  useEffect(() => {
    const map = new MLMap({
      container: el.current!,
      style: { version: 8, sources: { base: baseSource(bm) }, layers: [{ id: "base", type: "raster", source: "base" }] },
      bounds: bbox ? (bbox as [number, number, number, number]) : undefined,
      fitBoundsOptions: { padding: 30 },
      center: bbox ? undefined : [10.73, 59.94], zoom: bbox ? undefined : 10,
      attributionControl: false, interactive, dragRotate: false, pitchWithRotate: false,
    } as any);
    mapRef.current = map;
    (window as any).__map = map; // last created map, for debugging / automated checks
    map.touchZoomRotate?.disableRotation();
    if (interactive) map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    if (!compact) map.addControl(new ScaleControl({ unit: "metric" }), "bottom-right");
    map.addControl(new AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => onReady?.(map));
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el.current!);
    const onBm = (e: Event) => {
      const id = (e as CustomEvent).detail as BasemapId;
      setBm(id);
      if (map.isStyleLoaded()) applyBasemap(map, id); else map.once("load", () => applyBasemap(map, id));
    };
    window.addEventListener(EVT, onBm);
    return () => { window.removeEventListener(EVT, onBm); ro.disconnect(); map.remove(); mapRef.current = null; };
  }, []);

  return (
    <div class={`map ${cls}`} style={style}>
      <div class="gl" ref={el} />
      <div class="seg basemap">
        {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => (
          <button key={id} class={id === bm ? "on" : ""} onClick={() => setBasemap(id)} title="Basemap (all maps)">
            {BASEMAPS[id].label}
          </button>
        ))}
      </div>
      {children}
    </div>
  );
}

/** Create or update a GeoJSON source. */
export function setData(map: MLMap, id: string, data: GeoJSON.GeoJSON) {
  const src = map.getSource(id) as GeoJSONSource | undefined;
  if (src) src.setData(data as any);
  else map.addSource(id, { type: "geojson", data: data as any });
}

export function addLayer(map: MLMap, layer: LayerSpecification) {
  if (!map.getLayer(layer.id)) map.addLayer(layer);
}

export function fitBBox(map: MLMap, b: BBox | null, opts: { padding?: number; maxZoom?: number; animate?: boolean } = {}) {
  if (!b) return;
  const pad = opts.padding ?? 40;
  const c = map.getContainer();
  const p = Math.min(pad, Math.floor(Math.min(c.clientWidth, c.clientHeight) / 4));
  map.fitBounds(new LngLatBounds([b[0], b[1]], [b[2], b[3]]), { padding: p, maxZoom: opts.maxZoom ?? 16, animate: opts.animate ?? false });
}

export const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
