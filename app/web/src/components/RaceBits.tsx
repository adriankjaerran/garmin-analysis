// Small pieces shared by the Races pages (pages/RacesPage.tsx, pages/CommunityRacesPage.tsx; lib/races.ts).
import { deltaText, idxClass, idxTitle, pctText, type IdxMode } from "../lib/races";
import { dur } from "../lib/format";

/** the % | time toggle (lib/races useIdxMode) */
export function IdxToggle({ mode, onMode }: { mode: IdxMode; onMode: (m: IdxMode) => void }) {
  const opts: { id: IdxMode; label: string; title: string }[] = [
    { id: "pct", label: "%", title: "Index in %: (predicted − official) / predicted; + = faster than predicted" },
    { id: "time", label: "time", title: "Official − predicted time; − = faster than predicted" },
  ];
  return <div class="seg">{opts.map((o) => <button key={o.id} class={mode === o.id ? "on" : ""} aria-pressed={mode === o.id}
    title={o.title} onClick={() => onMode(o.id)}>{o.label}</button>)}</div>;
}

/** the index of one run race, coloured (green = faster than predicted) */
export function IdxCell({ pct, delta, mode }: { pct?: number; delta?: number; mode: IdxMode }) {
  if (pct === undefined || delta === undefined) return <span class="muted">—</span>;
  return <span class={idxClass(pct)} title={idxTitle(pct, delta)}>{mode === "pct" ? pctText(pct) : deltaText(delta)}</span>;
}

/** a ski gap to a reference time: "+1:26" or "+0.6 %" (no colour: ski has no prediction) */
export function gapCell(gap_s: number, ref_s: number, mode: IdxMode): string {
  if (Math.round(gap_s) === 0) return "±0";
  const s = gap_s > 0 ? "+" : "−";
  return mode === "pct" ? `${s}${(Math.abs(gap_s) / ref_s * 100).toFixed(1)} %` : `${s}${dur(Math.abs(gap_s))}`;
}
