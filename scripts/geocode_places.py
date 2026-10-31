"""One-off reverse geocode of generically named start clusters (OpenStreetMap Nominatim).

    uv run scripts/geocode_places.py adrian              # fetch missing points (<= 1 request/s)
    uv run scripts/geocode_places.py adrian --dry-run    # list what would be fetched
    uv run scripts/geocode_places.py adrian --repick     # re-pick names from the cached raw responses (offline)

Reads cluster centres from data/<p>/groups.json (places whose name is generic or already geocoded) and
writes data/<p>/cache/geocode.json {"source", "points": [{"lat", "lon", "name", "kind", "raw"}]}, appended
after every response. A centre with a cached point within REUSE_M is never re-fetched. Only cluster centre
coordinates are sent. session_groups.py `name_places()` then uses the names (display names only; ids stay
the generic ids). Docs: docs/session-groups.md.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
URL = "https://nominatim.openstreetmap.org/reverse"
UA = "garmin-analysis/0.1 (adrian@datafabric.no)"
ZOOM = 18          # most detail: the response object can be a park / stadium / lake; the address chain
                   # always carries neighbourhood / suburb / city, so a coarser name is still available
MIN_GAP_S = 1.1    # Nominatim usage policy: max 1 request/s
REUSE_M = 150      # a cached point this close to a centre counts as that centre's answer

# the response object itself names the place if its category is one of these (and its type isn't rejected)
OBJ_CAT = {"leisure", "natural", "tourism", "place", "water", "waterway"}
OBJ_TYPE = {"stadium", "sports_centre", "park", "nature_reserve", "peak", "beach", "bay", "wood",
            "golf_course", "marina", "attraction", "viewpoint", "castle", "monument", "university", "college",
            "square", "station"}   # with OBJ_CAT categories; plus these types in any category (amenity=university, ...)
OBJ_BAD = {"house", "residential", "apartments", "building", "yes", "detached", "terrace", "parking",
           "parking_space", "bench", "waste_basket", "shelter", "toilets", "bicycle_parking", "post_box",
           "pitch", "playground", "fitness_station", "swimming_pool", "garden", "picnic_site", "picnic_table",
           "hotel", "guest_house", "hostel", "apartment", "chalet", "information", "artwork", "camp_pitch",
           "fuel", "supermarket", "convenience", "restaurant", "cafe", "fast_food", "bus_stop", "platform"}
TRAIL = {"path", "track", "footway", "bridleway", "cycleway"}   # named trails beat a town / municipality name
# address-chain keys: FINE (most specific first), then COARSE admin units
FINE = ["leisure", "natural", "locality", "neighbourhood", "quarter", "hamlet", "isolated_dwelling", "village", "suburb"]
DISTRICT = ["borough", "city_district"]
COARSE = ["town", "municipality", "city"]
# area names too large to tell places apart (Oslo's bydel Marka = all of Nordmarka / Østmarka): use the road
TOO_BIG = {"Marka"}


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def pick(raw: dict) -> tuple[str | None, str | None]:
    """Nominatim jsonv2 -> (name, kind). The object itself if it is a park / stadium / lake / locality ...,
    else the most specific area of the address chain (neighbourhood > ... > suburb > city district), else a named trail
    under the point, else (if the area was too big, e.g. "Marka") the road, else town / municipality / city,
    else the road."""
    if not raw or raw.get("error"):
        return None, None
    nm, cat, typ = (raw.get("name") or "").strip(), raw.get("category"), raw.get("type")
    if nm and typ not in OBJ_BAD and (cat in OBJ_CAT or typ in OBJ_TYPE):
        return nm, typ or cat
    addr = raw.get("address") or {}
    big = False
    for k in FINE:   # (a rejected object, e.g. a sand-volleyball pitch, also appears in the chain: skip it)
        v = addr.get(k)
        if v and v != nm:
            if v in TOO_BIG:
                big = True
                continue
            return v, k
    for k in DISTRICT:
        if addr.get(k) and addr[k] not in TOO_BIG:
            return addr[k], k
    if nm and cat == "highway" and typ in TRAIL:
        return nm, typ
    if big and addr.get("road"):
        return addr["road"], "road"
    for k in COARSE:
        if addr.get(k) and addr[k] not in TOO_BIG:
            return addr[k], k
    if addr.get("road"):
        return addr["road"], "road"
    return (nm or None), (typ or cat)


def load(f: Path) -> dict:
    if f.exists():
        x = json.loads(f.read_text() or "{}")
        if isinstance(x, dict):
            x.setdefault("points", [])
            return x
    return {"points": []}


def save(f: Path, x: dict):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(x, ensure_ascii=False, indent=1))
    tmp.replace(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repick", action="store_true", help="recompute name/kind from cached raw responses, no fetch")
    ap.add_argument("--limit", type=int, default=0, help="fetch at most N points")
    args = ap.parse_args()
    D = ROOT / "data" / args.person
    f = D / "cache" / "geocode.json"
    cache = load(f)
    cache["source"] = f"OpenStreetMap Nominatim reverse (zoom={ZOOM}), (c) OpenStreetMap contributors, ODbL"
    if args.repick:
        for p in cache["points"]:
            if "raw" in p:
                p["name"], p["kind"] = pick(p["raw"])
        save(f, cache)
        print(f"re-picked {len(cache['points'])} points -> {f}")
        return
    places = json.loads((D / "groups.json").read_text())["places"]
    todo = []
    for pid, p in places.items():
        if p.get("src") not in ("generic", "geocode"):
            continue
        c = (p["lat"], p["lon"])
        if any(hav(c, (q["lat"], q["lon"])) <= REUSE_M for q in cache["points"]):
            continue
        todo.append((pid, c))
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} centres to fetch ({len(cache['points'])} cached)")
    if args.dry_run:
        for pid, c in todo:
            print(f"  {pid:24s} {c[0]:.5f},{c[1]:.5f}")
        return
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "nb,no,en"})
    last = 0.0
    for i, (pid, c) in enumerate(todo):
        time.sleep(max(0.0, MIN_GAP_S - (time.time() - last)))
        last = time.time()
        try:
            r = s.get(URL, params={"format": "jsonv2", "lat": f"{c[0]:.5f}", "lon": f"{c[1]:.5f}",
                                   "zoom": ZOOM, "addressdetails": 1}, timeout=30)
        except requests.RequestException as e:
            print(f"  {pid}: {e} - stopping", file=sys.stderr)
            break
        if r.status_code in (403, 429) or r.status_code >= 500:
            print(f"  {pid}: HTTP {r.status_code} - stopping (cache kept)", file=sys.stderr)
            break
        raw = r.json() if r.ok else {"error": f"HTTP {r.status_code}"}
        keep = {k: raw.get(k) for k in ("category", "type", "addresstype", "name", "display_name", "address", "error")
                if raw.get(k) is not None}
        nm, kind = pick(keep)
        cache["points"].append({"lat": c[0], "lon": c[1], "name": nm, "kind": kind, "for": pid, "raw": keep})
        save(f, cache)
        print(f"  [{i + 1}/{len(todo)}] {pid:24s} -> {nm} ({kind})")
    print(f"{len(cache['points'])} points in {f}")


if __name__ == "__main__":
    main()
