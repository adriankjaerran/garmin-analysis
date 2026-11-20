"""Merge a second, separately recorded machine into a shared erg session (two machines, a fixed rotation).

Usage: uv run scripts/merge_second_machine.py 123062870 --from maren --order adrian,magnus,maren [--b ID] [--no-fetch] [--dry-run]

  123062870   the session on machine A (the recording everyone in --order already has a copy of)
  --from      whose Logbook has machine B (needs CONCEPT2_<FROM>_TOKEN in .env)
  --order     the rotation on machine A, first rower first (each person's place = their position here)
  --b         machine B's result id (default: their result of the same machine type that started closest to A's,
              within 15 min)

What it does:
  1. fetch_concept2.py <from> (skip with --no-fetch); copied sessions in their copied.json are kept
  2. finds machine B and prints the candidates
  3. sets "erg_shared": {"rotation": [n, place], "machine_b": {"id": B, "from": <from>}} for A in every person's
     session_overrides.json (an estimate from a monitor photo, "second_machine_m", is dropped: B is measured now)
  4. refresh_concept2.py <person> --no-fetch for everyone in --order
  5. prints each person's rounds: metres on A / B, total, pace, and how well B's turns match the rotation
B stays out of <from>'s activity list (it is part of the session). See docs/concept2.md#two-machines-both-recorded.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import concept2 as c2  # noqa: E402

MAX_GAP_S = 15 * 60


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        sys.exit(f"failed ({r.returncode}): {' '.join(cmd)}")


def when(r: dict) -> datetime:
    return datetime.strptime(r["date"][:19], "%Y-%m-%d %H:%M:%S")


def mmss(s: float) -> str:
    return c2.mmss(s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", help="machine A's result id")
    ap.add_argument("--from", dest="owner", required=True, help="whose Logbook has machine B")
    ap.add_argument("--order", required=True, help="rotation on machine A, comma-separated, first rower first")
    ap.add_argument("--b", type=int, help="machine B's result id (default: found by start time)")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    order = [p.strip().lower() for p in a.order.split(",") if p.strip()]
    owner = a.owner.lower()
    data = ROOT / "data"
    for p in order:
        if not (c2.c2_dir(data / p) / "results" / a.session / "result.json").exists():
            sys.exit(f"{p} has no copy of {a.session} (data/{p}/concept2/results/{a.session}); see docs/concept2.md")
    ra = c2.result(data / order[0], a.session)

    if not a.no_fetch and not a.dry_run:
        run([sys.executable, "scripts/fetch_concept2.py", owner])
    own = json.loads((c2.c2_dir(data / owner) / "results.json").read_text() or "[]")
    cands = sorted([r for r in own if str(r["id"]) != a.session and r.get("type") == ra.get("type")
                    and abs((when(r) - when(ra)).total_seconds()) <= MAX_GAP_S],
                   key=lambda r: abs((when(r) - when(ra)).total_seconds()))
    print(f"machine A: {a.session}  {ra['date']}  {ra['distance']} m  {ra.get('time_formatted') or mmss(ra['time'] / 10)}")
    for r in cands:
        print(f"  candidate B: {r['id']}  {r['date']}  {r['distance']} m  {r.get('time_formatted')}"
              f"  strokes: {'yes' if r.get('stroke_data') else 'no'}")
    bid = a.b or (cands[0]["id"] if cands else None)
    if not bid:
        sys.exit(f"no {ra.get('type')} result in {owner}'s Logbook within {MAX_GAP_S // 60} min of {ra['date']}"
                 " (fetched? recorded with ErgData?); pass --b ID")
    bdir = c2.c2_dir(data / owner) / "results" / str(bid)
    if not (bdir / "strokes.json").exists():
        sys.exit(f"{bid} has no stroke data (data/{owner}/concept2/results/{bid}/strokes.json): can't split it per person")
    print(f"machine B: {bid} (from {owner})")

    for place, p in enumerate(order, 1):
        f = data / p / "session_overrides.json"
        o = json.loads(f.read_text() or "{}") if f.exists() else {}
        cur = o.get(a.session) or {}
        es = cur.get("erg_shared") if isinstance(cur.get("erg_shared"), dict) else {}
        es = {k: v for k, v in es.items() if k not in ("second_machine_m", "who")}
        es |= {"rotation": [len(order), place], "machine_b": {"id": int(bid), "from": owner}}
        note = cur.get("note") or f"Shared {ra.get('type')} {ra['date'][:10]}, 2 machines"
        if "both recorded" not in note:
            note += f"; machine B recorded by {owner} ({bid}), both recorded"
        o[a.session] = {**cur, "erg_shared": es, "note": note}
        print(f"  {p}: place {place}/{len(order)}")
        if not a.dry_run:
            f.write_text(json.dumps(o, indent=1, ensure_ascii=False))
    if a.dry_run:
        return
    for p in order:
        run([sys.executable, "scripts/refresh_concept2.py", p, "--no-fetch"])

    print()
    for p in order:
        r = c2.load_result(data / p, a.session)
        sh = (r or {}).get("shared") or {}
        mb = sh.get("machine_b")
        if not mb:
            print(f"{p}: not merged (no rotation / strokes?)")
            continue
        parts = [q for x in sh["rounds"] for q in x[4]]
        mA = sum(q[2] for q in parts if q[4] == "A")
        mB = sum(q[2] for q in parts if q[4] == "B")
        work = sum(x[1] for x in sh["rounds"])
        print(f"{p:<10} {c2.workout_name(r):<28} A {mA:6.0f} m  B {mB:6.0f} m  = {mA + mB:6.0f} m in {mmss(work)}"
              f"  {mmss(work / (mA + mB) * 500)}/500 m")
    print(f"\nB vs the rotation: comes {mb['shift']} slot(s) after A, start offset {mmss(mb['offset_s'])},"
          f" fit {mb['fit']} (0 = perfect; > 0.003: check), B reads {(mb['scale'] - 1) * 100:+.1f} % vs A")


if __name__ == "__main__":
    main()
