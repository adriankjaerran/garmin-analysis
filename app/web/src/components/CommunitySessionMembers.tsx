// Community part of a session page: one table row per person (km, moving time, reps, average rep, rep pace, HR,
// form delta, effort; the name links to the activity), the guests as a text line, and the earlier sessions at the
// same place ("Last time here" + the list). Data: /api/community/sessions/{gid} (lib/communitySession.ts) plus the
// together session already on the page (leaderboard, race result).
import { withPerson } from "../lib/router";
import { dur, km, shortDate, speedText } from "../lib/format";
import { personName } from "../lib/people";
import { paceText, repTime, type TSession } from "../lib/together";
import { useCommunity, type CsForm, type CsSession } from "../lib/communitySession";
import { LastTimeHere } from "./CommunitySessionHighlights";
import "../pages/community-session.css";

const DISC: Record<string, string> = { interval: "interval", long: "long-distance", ski: "ski" };

/** "+2.4": session score minus the shape the day before (VDOT points); coloured only when |delta| ≥ 2 */
function FormCell({ f }: { f: CsForm | null | undefined }) {
  if (!f || f.delta === null || f.delta === undefined) return <span class="muted" title={f?.text ?? "No form score for this session (easy runs, hikes, rides and multi-sport files have none)"}>—</span>;
  const d = f.delta;
  if (f.confidence === "none") return <span class="muted" title="score unreliable: rejected by the fitness model (e.g. HR not maximal, a mislabelled session)">?</span>;
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  const tip = [
    `Session score ${f.score?.toFixed(1)} vs shape ${f.shape?.toFixed(1)} the day before (${DISC[f.discipline ?? ""] ?? f.discipline ?? ""} fitness, VDOT points)`,
    `confidence: ${f.confidence}`,
    f.flags?.length ? `flags: ${f.flags.join(", ").replace(/_/g, " ")}` : null,
  ].filter(Boolean).join("\n");
  return <span class={d >= 2 ? "cm-up" : d <= -2 ? "cm-down" : ""} title={tip}>{sign}{Math.abs(d).toFixed(1)}</span>;
}

export function CommunitySessionMembers({ gid, s }: { gid: string; s: TSession }) {
  const { data: c } = useCommunity(gid);
  if (!c) return null;
  const order = s.participants.map((p) => p.key);
  const members = [...c.members].sort((a, b) => (order.indexOf(a.key) + 1 || 99) - (order.indexOf(b.key) + 1 || 99));
  const iv = !!s.intervals;
  const races = s.continuous?.race ?? [];
  const anyEff = !!s.intervals?.leaderboard.some((l) => l.avg_effort_pct);
  return (
    <section class="cs-who">
      <div class="tg-mxwrap">
        <table class="tg-tbl cs-ppl">
          <thead><tr>
            <th>Person</th><th>Workout</th><th class="num">km</th><th class="num" title="Moving time">Moving</th>
            {iv ? <>
              <th class="num" title="Every full rep they ran">Reps</th>
              <th class="num" title="Average time per rep (suspect reps left out)">Avg rep</th>
              <th class="num" title="Total distance ÷ total time of their own reps">Rep pace</th>
            </> : <>
              {races.length > 0 && <th class="num">Race time</th>}
              <th class="num" title={races.length ? "Race pace" : "Distance ÷ moving time"}>Pace</th>
            </>}
            <th class="num" title="Average heart rate (bpm)">HR</th>
            <th class="num" title="Session score minus shape the day before (VDOT points)">vs form</th>
            {anyEff && <th class="num" title="Average peak HR per rep as % of max HR (or HR reserve)">Effort</th>}
          </tr></thead>
          <tbody>
            {members.map((m) => {
              const p = s.participants.find((x) => x.key === m.key);
              const lb = s.intervals?.leaderboard.find((l) => l.person === m.key);
              const race = races.find((r) => r.person === m.key);
              const n = m.numbers;
              const sport = p?.sport && p.sport !== "other" ? p.sport : s.group.sport;   // a multisport file in a run session: run pace
              const role = m.role === "partial" ? " (part of the workout)" : m.role === "other" ? " (other workout)" : "";
              return (
                <tr key={m.key} class={m.role === "other" ? "muted" : ""}>
                  <td><a href={withPerson(`/activities/${m.activity_id}`, m.person)} title={`${personName(m.key)}'s activity`}>{personName(m.key)}</a>{m.key === c.viewer && <span class="muted"> (you)</span>}</td>
                  <td title={m.type ?? undefined}>{m.workout ?? "—"}<span class="muted">{role}</span></td>
                  <td class="num">{n?.km ? km(n.km) : "—"}</td>
                  <td class="num" title={n?.dur_s ? `elapsed ${dur(n.dur_s)}` : undefined}>{dur(n?.moving_s)}</td>
                  {iv ? <>
                    <td class="num">{m.reps_done ?? "—"}</td>
                    <td class="num">{m.avg_s ? repTime(m.avg_s) : "—"}</td>
                    <td class="num">{m.pace_s_km ? paceText(m.pace_s_km, sport) : "—"}</td>
                  </> : <>
                    {races.length > 0 && <td class="num">{race ? dur(race.time_s) : "—"}</td>}
                    <td class="num">{race?.dist_km ? speedText(sport, (race.dist_km * 1000) / race.time_s) : n?.km && n?.moving_s ? speedText(sport, (n.km * 1000) / n.moving_s) : "—"}</td>
                  </>}
                  <td class="num">{n?.avg_hr ? Math.round(n.avg_hr) : "—"}</td>
                  <td class="num"><FormCell f={m.form} /></td>
                  {anyEff && <td class="num">{lb?.avg_effort_pct ? `${lb.avg_effort_pct} %` : "—"}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {c.meta.guests.length > 0 && <p class="small"><span class="muted">Guests:</span> {c.meta.guests.join(", ")}</p>}
    </section>
  );
}

/** the last session here (with each member's score then and now) and the other sessions at the same place */
export function SamePlace({ gid }: { gid: string }) {
  const { data: c } = useCommunity(gid);
  if (!c || !c.same_place.length) return null;
  return (
    <div class="cs-same">
      <LastTimeHere c={c} base="/community" />
      <SameList c={c} />
    </div>
  );
}

function SameList({ c }: { c: CsSession }) {
  const label = (id: string) => c.categories.find((x) => x.id === id)?.label ?? id;
  return (
    <section>
      <h2>Same place before <span class="muted small">· {c.same_place.length} other session{c.same_place.length === 1 ? "" : "s"} at {c.place?.name ?? "this place"}</span></h2>
      <table class="tg-tbl">
        <tbody>
          {c.same_place.map((x) => (
            <tr key={x.gid}>
              <td class="nowrap">{shortDate(x.date, c.date)}</td>
              <td><a href={`/community/${encodeURIComponent(x.gid)}`}>{x.workout}</a></td>
              <td>{label(x.category)}</td>
              <td>{x.people.map(personName).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
