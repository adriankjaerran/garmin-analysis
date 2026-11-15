// Everyone's splits side by side on an interval session: per rep and person the time, pace and heart rate (avg,
// peak in the tooltip), every rep each person ran (also the ones only they ran), the fastest of each rep marked,
// and an average row. Continuous runs / races / hikes already have "Splits per km" on the page.
import { useMemo, useState } from "preact/hooks";
import { repLabel, speedVal, speedUnit } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { repTime, type Cell, type Intervals, type TSession } from "../lib/together";
import "../pages/community-session.css";

type Col = "t" | "p" | "h";
/** what a rep result shows: own time, or + the gap to the rep's leader / to the one just ahead */
export type GapMode = "time" | "leader" | "next";
const GAP_MODES: { id: GapMode; label: string; title: string }[] = [
  { id: "time", label: "Time", title: "Own time only" },
  { id: "leader", label: "+Leader", title: "Behind the fastest of the rep" },
  { id: "next", label: "+Next", title: "Behind the one just ahead" }];
export function useGapMode(key: string, dflt: GapMode): [GapMode, (m: GapMode) => void] {
  const [m, setM] = useState<GapMode>(() => (localStorage.getItem(key) as GapMode | null) ?? dflt);
  return [m, (v: GapMode) => { setM(v); localStorage.setItem(key, v); }];
}
export function GapToggle({ mode, onMode }: { mode: GapMode; onMode: (m: GapMode) => void }) {
  return <div class="seg">{GAP_MODES.map((g) => <button key={g.id} class={mode === g.id ? "on" : ""} aria-pressed={mode === g.id}
    title={g.title} onClick={() => onMode(g.id)}>{g.label}</button>)}</div>;
}
const VIEWS: { id: "all" | Col; label: string }[] = [{ id: "all", label: "All" }, { id: "t", label: "Time" }, { id: "p", label: "Pace" }, { id: "h", label: "HR" }];

type N = number | null;
/** time run: partial -> own time; a set-time rep -> the set time (result_s is a ranking equivalent there) */
const tOf = (c: Cell | null | undefined, timeSlot = false): N => (c ? (c.partial ? c.own_time_s ?? c.time_s : timeSlot ? c.time_s : c.result_s ?? c.time_s ?? c.own_time_s) : null);
const dOf = (c: Cell | null | undefined): N => (c ? (c.partial ? c.own_dist_m : c.dist_m ?? c.own_dist_m) : null);

/** warm-up / cool-down rows on or off (shared by Rep by rep and Splits, remembered) */
export function WarmupToggle({ iv, show, onShow }: { iv: Intervals; show: boolean; onShow: (v: boolean) => void }) {
  if (!iv.slots.some((sl) => sl.warmup)) return null;
  return <div class="seg"><button class={show ? "on" : ""} aria-pressed={show} onClick={() => onShow(!show)}
    title={show ? "Hide warm-up / cool-down reps" : "Show warm-up / cool-down reps"}>Warm-ups</button></div>;
}

export function CommunitySessionSplits({ s, showWu = true, onShowWu }: { s: TSession; showWu?: boolean; onShowWu?: (v: boolean) => void }) {
  const theme = useTheme();
  const [view, setView] = useState<"all" | Col>(() => (localStorage.getItem("session.splitCols") as "all" | Col | null) ?? "all");
  const pickView = (v: "all" | Col) => { setView(v); localStorage.setItem("session.splitCols", v); };
  const [gapMode, setGapMode] = useGapMode("session.splitGap", "time");
  const cols: Col[] = view === "all" ? ["t", "p", "h"] : [view];
  const has = (c: Col) => cols.includes(c);
  const iv = s.intervals;
  const sport = s.group.sport;
  // column order: dragged by the user (remembered per session), else the leaderboard (fastest to the left)
  const okey = `session.splitOrder.${s.gid}`;
  const [custom, setCustom] = useState<string[] | null>(() => { try { return JSON.parse(localStorage.getItem(okey) ?? "null"); } catch { return null; } });
  const [drag, setDrag] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const all = iv ? Object.keys(iv.reps) : [];
  const byRank = iv ? [...iv.leaderboard.map((l) => l.person).filter((k) => all.includes(k)), ...all.filter((k) => !iv.leaderboard.some((l) => l.person === k))] : [];
  const keys = custom ? [...custom.filter((k) => all.includes(k)), ...byRank.filter((k) => !custom.includes(k))] : byRank;
  const moveTo = (from: string, to: string) => {
    if (from === to) return;
    const o = keys.filter((k) => k !== from);
    o.splice(o.indexOf(to) + (keys.indexOf(from) < keys.indexOf(to) ? 1 : 0), 0, from);
    setCustom(o); localStorage.setItem(okey, JSON.stringify(o));
  };
  const resetOrder = () => { setCustom(null); localStorage.removeItem(okey); };
  const rows = useMemo(() => {
    if (!iv) return [];
    return iv.slots.map((sl) => {
      const timeSlot = sl.target?.kind === "time";
      const cells = Object.fromEntries(keys.map((k) => {
        const c = iv.reps[k][sl.slot];
        const t = tOf(c, timeSlot), d = dOf(c);
        return [k, c && t ? { c, t, d, ms: d && t ? d / t : null } : null];
      }));
      // fastest: the shortest time over a set distance, the longest distance in a set time
      const vals = keys.map((k) => cells[k]).filter((v) => v && !v.c.suspect && !v.c.partial);
      let best: string | null = null;
      if (vals.length >= 2) {
        const pick = timeSlot ? (a: any, b: any) => (b.d ?? 0) - (a.d ?? 0) : (a: any, b: any) => a.t - b.t;
        const top = [...keys.filter((k) => cells[k] && !cells[k]!.c.suspect && !cells[k]!.c.partial)].sort((a, b) => pick(cells[a], cells[b]))[0];
        best = top ?? null;
      }
      return { sl, timeSlot, cells, best };
    });
  }, [iv, theme, keys.join()]);
  if (!iv || !rows.length || !keys.length) return null;
  const shown = rows.filter((r) => showWu || !r.sl.warmup);
  const many = keys.length > 3;
  // the Avg row = the leaderboard's numbers (every full rep the person ran, suspect ones left out), so they agree
  const avg = (k: string) => {
    const xs = rows.map((r) => r.cells[k]).filter((v): v is NonNullable<typeof v> => !!v && !v.c.suspect && !v.c.partial);
    const lb = iv!.leaderboard.find((l) => l.person === k);
    const hrs = xs.map((v) => v.c.hr).filter((h): h is number => !!h);
    return { n: lb?.n_counted ?? xs.length, t: lb?.avg_s ?? null, ms: lb?.pace_s_km ? 1000 / lb.pace_s_km : null, hr: hrs.length ? hrs.reduce((a, b) => a + b, 0) / hrs.length : null };
  };
  return (
    <section class="cs-splits">
      <div class="head"><h2>Splits</h2>
        <div class="tools">
          {onShowWu && <WarmupToggle iv={iv} show={showWu} onShow={onShowWu} />}
          {custom && <button class="link" onClick={resetOrder} title="Back to fastest first">Reset order</button>}
          {has("t") && <GapToggle mode={gapMode} onMode={setGapMode} />}
          <div class="seg">{VIEWS.map((v) => <button key={v.id} class={view === v.id ? "on" : ""} aria-pressed={view === v.id} onClick={() => pickView(v.id)}>{v.label}</button>)}</div>
        </div></div>
      <div class="tg-mxwrap">
        <table class="tg-tbl cs-sp">
          <thead>
            <tr><th rowSpan={2}>Rep</th><th rowSpan={2} class="set">Set</th>
              {keys.map((k) => <th key={k} colSpan={cols.length} draggable title="Drag to reorder"
                class={"pgrp drag" + (drag === k ? " dragging" : "") + (over === k && drag && drag !== k ? " over" : "")} style={{ borderBottomColor: personColour(k) }}
                onDragStart={(e) => { setDrag(k); e.dataTransfer?.setData("text/plain", k); if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"; }}
                onDragOver={(e) => { e.preventDefault(); if (over !== k) setOver(k); }}
                onDragLeave={() => over === k && setOver(null)}
                onDrop={(e) => { e.preventDefault(); const f = drag ?? e.dataTransfer?.getData("text/plain"); if (f) moveTo(f, k); setDrag(null); setOver(null); }}
                onDragEnd={() => { setDrag(null); setOver(null); }}>{personName(k)}</th>)}</tr>
            <tr>{keys.map((k, i) => [has("t") && <th key={k + "t"} class={"num" + (i > 0 && cols[0] === "t" ? " sep" : "")}>Time</th>,
              has("p") && <th key={k + "p"} class={"num" + (i > 0 && cols[0] === "p" ? " sep" : "")}>{speedUnit(sport) === "km/h" ? "km/h" : "Pace"}</th>,
              has("h") && <th key={k + "h"} class={"num" + (i > 0 && cols[0] === "h" ? " sep" : "")}>HR</th>])}</tr>
          </thead>
          <tbody>
            {shown.map(({ sl, timeSlot, cells, best }) => {
              const ranked = timeSlot ? [] : keys.filter((k) => cells[k] && !cells[k]!.c.partial && !cells[k]!.c.suspect).sort((a, b) => cells[a]!.t - cells[b]!.t);
              const gapOf = (k: string): string | null => {
                const i = ranked.indexOf(k);
                if (gapMode === "time" || i <= 0) return null;
                const ref = gapMode === "leader" ? cells[ranked[0]]!.t : cells[ranked[i - 1]]!.t;
                return `+${(cells[k]!.t - ref).toFixed(1)}`;
              };
              return (
              <tr key={sl.slot} class={(!sl.anchor ? "extra" : "") + (sl.warmup ? " warmup" : "")} title={sl.warmup ? `${sl.warmup}: not counted` : undefined}>
                <td class="rl">{repLabel(sl.label, true)}</td>
                <td class="muted set">{sl.target?.text ?? ""}</td>
                {keys.map((k, i) => {
                  const v = cells[k];
                  const sepOf = (c: Col) => (i > 0 && cols[0] === c ? " sep" : "");
                  if (!v) return cols.map((c, j) => <td key={k + c} class={(j === 0 ? "num muted" : "") + sepOf(c)}>{j === 0 ? "—" : ""}</td>);
                  const tip = [v.c.partial ? "partial rep (not ranked)" : null, v.c.suspect ? `suspect: ${v.c.suspect}` : null,
                    v.d ? `${Math.round(v.d)} m` : null, v.c.own_dist_m && v.d !== v.c.own_dist_m ? `GPS ${Math.round(v.c.own_dist_m)} m` : null].filter(Boolean).join(" · ");
                  return [
                    has("t") && <td key={k + "t"} class={"num" + sepOf("t") + (best === k ? " best" : "") + (v.c.partial || v.c.suspect ? " muted" : "")} title={tip}>
                      {timeSlot ? (v.d ? `${Math.round(v.d)} m` : "—") : repTime(v.t)}{v.c.suspect ? "?" : v.c.partial ? "*" : ""}
                      {gapOf(k) && <span class="gap">{gapOf(k)}</span>}</td>,
                    has("p") && <td key={k + "p"} class={"num" + sepOf("p") + (view === "p" && best === k ? " best" : "")}>{speedVal(sport, v.ms)}</td>,
                    has("h") && <td key={k + "h"} class={"num" + sepOf("h") + (view === "h" ? "" : " muted")} title={v.c.hr_peak ? `avg ${v.c.hr ?? "—"} · peak ${v.c.hr_peak} bpm${v.c.effort_pct ? ` (${v.c.effort_pct} %)` : ""}` : undefined}>{v.c.hr ?? "—"}</td>,
                  ];
                })}
              </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td><b>Avg</b></td><td class="set" />
              {keys.map((k, i) => {
                const a = avg(k);
                const sepOf = (c: Col) => (i > 0 && cols[0] === c ? " sep" : "");
                return [has("t") && <td key={k + "t"} class={"num" + sepOf("t")} title={`${a.n} full reps (suspect ones left out) · the same numbers as the leaderboard`}><b>{a.t ? repTime(a.t) : `${a.n} reps`}</b></td>,
                  has("p") && <td key={k + "p"} class={"num" + sepOf("p")}><b>{speedVal(sport, a.ms)}</b></td>,
                  has("h") && <td key={k + "h"} class={"num" + sepOf("h") + (view === "h" ? "" : " muted")}>{a.hr ? Math.round(a.hr) : "—"}</td>];
              })}
            </tr>
          </tfoot>
        </table>
      </div>
      {many && <p class="note small">Scroll sideways for everyone.</p>}
    </section>
  );
}
