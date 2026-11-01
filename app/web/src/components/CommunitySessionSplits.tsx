// Everyone's splits side by side on an interval session: per rep and person the time, pace and heart rate (avg,
// peak in the tooltip), every rep each person ran (also the ones only they ran), the fastest of each rep marked,
// and an average row. Continuous runs / races / hikes already have "Splits per km" on the page.
import { useMemo } from "preact/hooks";
import { speedVal, speedUnit } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { repTime, type Cell, type TSession } from "../lib/together";
import { Av } from "./CommunityBits";
import "../pages/community-session.css";

type N = number | null;
const tOf = (c: Cell | null | undefined): N => (c ? (c.partial ? c.own_time_s ?? c.time_s : c.result_s ?? c.time_s ?? c.own_time_s) : null);
const dOf = (c: Cell | null | undefined): N => (c ? (c.partial ? c.own_dist_m : c.dist_m ?? c.own_dist_m) : null);

export function CommunitySessionSplits({ s }: { s: TSession }) {
  const theme = useTheme();
  const iv = s.intervals;
  const sport = s.group.sport;
  const keys = iv ? Object.keys(iv.reps) : [];
  const rows = useMemo(() => {
    if (!iv) return [];
    return iv.slots.map((sl) => {
      const timeSlot = sl.target?.kind === "time";
      const cells = Object.fromEntries(keys.map((k) => {
        const c = iv.reps[k][sl.slot];
        const t = tOf(c), d = dOf(c);
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
  }, [iv, theme]);
  if (!iv || !rows.length || !keys.length) return null;
  const many = keys.length > 3;
  const avg = (k: string) => {
    const xs = rows.map((r) => r.cells[k]).filter((v): v is NonNullable<typeof v> => !!v && !v.c.suspect && !v.c.partial);
    const t = xs.reduce((a, v) => a + v.t, 0), d = xs.reduce((a, v) => a + (v.d ?? 0), 0);
    const hrs = xs.map((v) => v.c.hr).filter((h): h is number => !!h);
    const sameT = new Set(rows.filter((r) => r.cells[k]).map((r) => r.sl.target?.text)).size === 1;
    return { n: xs.length, t: xs.length && sameT ? t / xs.length : null, ms: d && t ? d / t : null, hr: hrs.length ? hrs.reduce((a, b) => a + b, 0) / hrs.length : null };
  };
  return (
    <section class="cs-splits">
      <div class="head"><h2>Splits</h2><span class="sub">every rep each person ran: time, pace ({speedUnit(sport)}) and average heart rate (peak in the tooltip) · ▲ fastest of the rep</span></div>
      <div class="tg-mxwrap">
        <table class="tg-tbl cs-sp">
          <thead>
            <tr><th rowSpan={2}>Rep</th><th rowSpan={2} class="set">Set</th>
              {keys.map((k) => <th key={k} colSpan={3} class="pgrp" style={{ borderBottomColor: personColour(k) }}><span class="pn"><Av p={k} size="sm" />{personName(k)}</span></th>)}</tr>
            <tr>{keys.map((k) => [<th key={k + "t"} class="num">Time</th>, <th key={k + "p"} class="num">{speedUnit(sport) === "km/h" ? "km/h" : "Pace"}</th>, <th key={k + "h"} class="num">HR</th>])}</tr>
          </thead>
          <tbody>
            {rows.map(({ sl, timeSlot, cells, best }) => (
              <tr key={sl.slot} class={!sl.anchor ? "extra" : ""}>
                <td class="rl">R{sl.label}</td>
                <td class="muted set">{sl.target?.text ?? ""}</td>
                {keys.map((k, i) => {
                  const v = cells[k];
                  const sep = i > 0 ? " sep" : "";
                  if (!v) return [<td key={k + "t"} class={"num muted" + sep}>—</td>, <td key={k + "p"} />, <td key={k + "h"} />];
                  const tip = [v.c.partial ? "partial rep (not ranked)" : null, v.c.suspect ? `suspect: ${v.c.suspect}` : null,
                    v.d ? `${Math.round(v.d)} m` : null, v.c.own_dist_m && v.d !== v.c.own_dist_m ? `GPS ${Math.round(v.c.own_dist_m)} m` : null].filter(Boolean).join(" · ");
                  return [
                    <td key={k + "t"} class={"num" + sep + (best === k ? " best" : "") + (v.c.partial || v.c.suspect ? " muted" : "")} title={tip}>
                      {best === k && <span class="cs-best" style={{ color: personColour(k) }}>▲</span>}
                      {timeSlot ? (v.d ? `${Math.round(v.d)} m` : "—") : repTime(v.t)}{v.c.suspect ? "?" : v.c.partial ? "◐" : ""}</td>,
                    <td key={k + "p"} class="num">{speedVal(sport, v.ms)}</td>,
                    <td key={k + "h"} class="num muted" title={v.c.hr_peak ? `avg ${v.c.hr ?? "—"} · peak ${v.c.hr_peak} bpm${v.c.effort_pct ? ` (${v.c.effort_pct} %)` : ""}` : undefined}>{v.c.hr ?? "—"}</td>,
                  ];
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td><b>Avg</b></td><td class="set" />
              {keys.map((k, i) => {
                const a = avg(k);
                return [<td key={k + "t"} class={"num" + (i > 0 ? " sep" : "")} title={`${a.n} full reps`}><b>{a.t ? repTime(a.t) : `${a.n} reps`}</b></td>,
                  <td key={k + "p"} class="num"><b>{speedVal(sport, a.ms)}</b></td>, <td key={k + "h"} class="num muted">{a.hr ? Math.round(a.hr) : "—"}</td>];
              })}
            </tr>
          </tfoot>
        </table>
      </div>
      {many && <p class="note small">Scroll sideways for everyone.</p>}
    </section>
  );
}
