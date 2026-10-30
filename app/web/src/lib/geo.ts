export type BBox = [number, number, number, number]; // west, south, east, north

/** Google encoded polyline (precision 5) -> [lon, lat][] */
export function decodePolyline(s: string, precision = 5): [number, number][] {
  const f = Math.pow(10, precision);
  const out: [number, number][] = [];
  let i = 0, lat = 0, lon = 0;
  while (i < s.length) {
    for (let k = 0; k < 2; k++) {
      let shift = 0, res = 0, b;
      do { b = s.charCodeAt(i++) - 63; res |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      const d = res & 1 ? ~(res >> 1) : res >> 1;
      if (k === 0) lat += d; else lon += d;
    }
    out.push([lon / f, lat / f]);
  }
  return out;
}

export function unionBBox(boxes: BBox[]): BBox | null {
  if (!boxes.length) return null;
  return boxes.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);
}

export function bboxOfCoords(c: [number, number][]): BBox | null {
  if (!c.length) return null;
  let w = 180, s = 90, e = -180, n = -90;
  for (const [x, y] of c) { if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y; }
  return [w, s, e, n];
}

/** Colour ramp slow -> fast (or low -> high HR). */
export const RAMP = ["#313695", "#4575b4", "#74add1", "#fdae61", "#f46d43", "#a50026"];

export function quantile(xs: number[], q: number): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.floor(q * (s.length - 1))))];
}
