// MapLibre wrapper: raster basemap (Kartverket topo / grey, OpenStreetMap, OpenTopoMap) with a small dropdown that
// is shared by every map in the app (localStorage), plus small helpers for GeoJSON sources.
import { Map as MLMap, NavigationControl, ScaleControl, AttributionControl, LngLatBounds, setWorkerUrl } from "maplibre-gl";
// MapLibre 6 loads its worker as a separate module next to the bundle; let Vite emit it and point at it
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { GeoJSONSource, LayerSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { BBox } from "../lib/geo";
import { currentTheme, useTheme } from "../lib/theme";

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

/** rough outline of mainland Norway (+ ~15 km on the land side): Kartverket's cache has tiles only inside */
const NORWAY: [number, number][] = [[4, 57.7], [7, 57.7], [8.5, 58], [10.6, 58.7], [11.2, 58.85], [11.75, 58.9], [12.1, 59.3], [12, 59.9],
  [12.75, 60.2], [12.8, 60.7], [12.5, 61.1], [12.6, 61.6], [12.4, 62.3], [12.35, 62.9], [12.4, 63.6], [13, 64], [14.4, 64.3], [14.3, 65.1],
  [14.8, 65.7], [15.6, 66.2], [16.6, 67], [16.8, 67.5], [18.2, 68.2], [18.4, 68.6], [20.2, 68.55], [20.4, 69], [21.3, 69.3], [22.2, 68.9],
  [23.3, 68.6], [24.9, 68.5], [25.9, 69], [26.6, 69.6], [27.9, 70], [28.8, 69.7], [29.1, 69.2], [28.9, 69], [29.6, 69], [31.5, 69.6],
  [31.5, 71.5], [4, 71.5]];
export function inNorway(lon: number, lat: number): boolean {
  let inside = false;
  for (let i = 0, j = NORWAY.length - 1; i < NORWAY.length; j = i++) {
    const [xi, yi] = NORWAY[i], [xj, yj] = NORWAY[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
const isKV = (id: BasemapId) => id === "topo" || id === "grey";

const KEY = "garmin.basemap";
const EVT = "app:basemap";
/** the stored choice, else the map's default — except that the bright Topo default becomes Grey in the dark theme
 *  (a beige topo map next to the dark UI is glaring); a stored choice always wins */
export const getBasemap = (fallback: BasemapId = "topo"): BasemapId => {
  const v = localStorage.getItem(KEY) as BasemapId | null;
  if (v && v in BASEMAPS) return v;
  return fallback === "topo" && currentTheme() === "dark" ? "grey" : fallback;
};
/** dark theme: the raster tiles are dimmed a little so they don't glare (they stay light: track colours are for light maps) */
const DIM = { dark: { "raster-brightness-max": 0.86, "raster-contrast": -0.05 }, light: { "raster-brightness-max": 1, "raster-contrast": 0 } };
function dimBase(map: MLMap) {
  if (!map.getLayer("base")) return;
  const d = DIM[currentTheme()];
  for (const [k, v] of Object.entries(d)) map.setPaintProperty("base", k as any, v);
}
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
  dimBase(map);
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
  // Kartverket outside Norway is blank: this map shows OSM instead while its centre is abroad (the stored
  // choice is kept; picking a basemap in this map's dropdown turns the automatic switch off for it)
  const [auto, setAuto] = useState(false);
  const pinned = useRef(false);
  const mapRef = useRef<MLMap | null>(null);
  const theme = useTheme();
  const setWant = useRef<((id: BasemapId) => void) | null>(null);
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    if (m.isStyleLoaded()) dimBase(m); else m.once("load", () => dimBase(m));
    // the default (not a stored choice) follows the theme: Topo in light, Grey in dark
    if (!localStorage.getItem(KEY)) setWant.current?.(getBasemap(defaultBasemap));
  }, [theme]);

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
    let want: BasemapId = bm, shown: BasemapId = bm, loaded = false;
    const show = (id: BasemapId) => { if (id !== shown) { shown = id; applyBasemap(map, id); } setAuto(id !== want); };
    const check = () => {
      if (!loaded) return;
      const c = map.getCenter();
      show(isKV(want) && !pinned.current && !inNorway(c.lng, c.lat) ? "osm" : want);
    };
    map.on("load", () => { loaded = true; dimBase(map); onReady?.(map); check(); });
    map.on("moveend", check);
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el.current!);
    const onBm = (e: Event) => {
      want = (e as CustomEvent).detail as BasemapId;
      setBm(want);
      check();
    };
    setWant.current = (id) => { want = id; setBm(id); check(); };
    window.addEventListener(EVT, onBm);
    return () => { window.removeEventListener(EVT, onBm); ro.disconnect(); map.remove(); mapRef.current = null; };
  }, []);

  return (
    <div class={`map ${cls}`} style={style}>
      <div class="gl" ref={el} />
      <select class="basemap" value={auto ? "osm" : bm} aria-label="Basemap (all maps)"
        title={auto ? "OSM shown automatically: Kartverket maps cover Norway only (pick Topo / Grey to force them)" : "Basemap (all maps)"}
        onChange={(e) => { const id = (e.target as HTMLSelectElement).value as BasemapId; pinned.current = isKV(id) && auto; setBasemap(id); }}>
        {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => <option key={id} value={id}>{BASEMAPS[id].label}</option>)}
      </select>
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
