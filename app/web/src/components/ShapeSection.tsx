// Activity page "Shape" section: what this session says about fitness, in plain words — the one-liner, the score,
// how it compares with your shape that day, and race equivalents. Model internals (evidence weight, σ, the
// with-HR variant, components, HR diagnostics) are behind "Details".
import { useApi } from "../lib/api";
import { DISC_LABEL, KIND_HELP, KIND_LABEL, daysAgo, discColor, pm, raceT, unitShort, type FitnessSession } from "../lib/fitness";
import "../fitness.css";

const HR_KEYS = ["work_hrr", "hrr"];
const STALE_DAYS = 90;   // as the Fitness page: a shape without a race / hard session for longer is greyed
/** what the kind of evidence means for the shape, in plain words */
const COUNTS: Record<string, string> = { race: "a race: counts fully", hard: "a hard session: counts fully unless far below your shape",
  bound: "an easy or long session: only counts if it shows more than your shape", implausible: "ignored (faster than your shape allows: bad GPS or mislabelled)" };

/** `not_scored` (WP-A): why the model skipped this activity */
type SessionResp = FitnessSession & { not_scored?: { key: string; text: string } | null };

/** "1.3 above your shape" / "0.4 below" / "at your shape"; a lower bound (easy / long run) below the shape says
 *  nothing about it: "Easy effort: doesn't change your shape" (no number, it isn't a shortfall) */
export function vsShape(d: number | null, kind?: string): string {
  if (d === null) return "—";
  if (kind === "bound" && d < 0) return "Easy effort: doesn't change your shape";
  const r = Math.abs(d).toFixed(1);
  return r === "0.0" ? "right at your shape" : `${r} ${d > 0 ? "above" : "below"} your shape that day`;
}

export function ShapeSection({ id }: { id: string }) {
  const { data } = useApi<SessionResp>(`/api/fitness/session/${id}`);
  // no fitness.json, or nothing to say (unscored sport without HR: a swim, a walk) -> no section
  if (!data || (!data.scored && !data.hr_diagnostics)) return null;
  const hd = data.hr_diagnostics;
  // HR from the scored part (work bouts / best window) first, then the steady-chunk diagnostics
  const hrRows = [
    ...data.sessions.flatMap((s) => s.components_explained.filter((c) => HR_KEYS.includes(c.key)).map((c) => ({
      ...c, label: c.key === "work_hrr" ? "Effort in the reps" : "Effort in the window",
      meaning: c.key === "work_hrr" ? "mean heart rate during the reps, as % of your heart-rate reserve" : `mean heart rate over the scored ${DISC_LABEL[s.discipline].toLowerCase()} stretch, % of heart-rate reserve` }))).slice(0, 1),
    ...(hd?.explained.filter((c) => c.text !== "—") ?? []),
  ];
  return (
    <section>
      <div class="head">
        <h2>Shape</h2>
        <span class="sub">what this workout says about your fitness (<a href="/fitness">Fitness</a> page)</span>
      </div>
      {!data.scored && <div class="empty">Not used for your fitness estimate: {data.not_scored?.text ?? "only interval, race, run and ski sessions with a watch file are."}</div>}
      {data.sessions.map((s) => {
        const eq = s.race_equivalents_s, sh = s.shape;
        const delta = s.shape_at !== null && s.kind !== "implausible" ? s.score - s.shape_at : null;
        return (
          <div class="shape-block" key={s.discipline}>
            <p class="shape-line">
              <span class="dot" style={{ background: discColor(s.discipline) }} />
              {s.explain}
            </p>
            <dl class="kv">
              <dt>Fitness score</dt><dd><b>{s.score.toFixed(1)}</b> {unitShort(s.discipline)} <span class="muted">{s.label}</span></dd>
              <dt>Compared with you</dt><dd title={s.kind === "bound" && delta !== null && delta < 0 ? `the score is a lower bound (${delta.toFixed(1)} vs your shape): an easy or long run isn't all-out, so it only counts when it shows more than your shape` : undefined}>{vsShape(delta, s.kind)}</dd>
              <dt>Race equivalents</dt><dd>{eq ? `5k ${raceT(eq["5k"])} · 10k ${raceT(eq["10k"])} · half ${raceT(eq.half)}` : <span class="muted">{s.discipline === "ski" ? "none (ski has its own scale)" : "—"}</span>}</dd>
              <dt>How it counts</dt><dd title={KIND_HELP[s.kind]}>{COUNTS[s.kind] ?? KIND_LABEL[s.kind]}</dd>
              <dt>Your shape that day</dt><dd>{sh ? <>{pm(s.shape_at ?? sh.mean, sh.sd)}{s.shape_race_equivalents_s ? <span class="muted"> · 5k {raceT(s.shape_race_equivalents_s["5k"])}</span> : null}</> : "—"}</dd>
              <dt>Your shape now</dt><dd>{s.current ? (() => {
                const c = s.current, ago = c.last_evidence ? daysAgo(c.last_evidence.date, c.date) : null;
                // stale: no race / hard session for > 90 days: the value is carried forward, greyed, its band grows
                return ago === null || ago > STALE_DAYS
                  ? <span style={{ opacity: 0.6 }} title="no recent race or hard session: the model carries the last value forward and its uncertainty grows every day">
                      {pm(c.mean, c.sd)} <span class="muted">· last evidence {ago === null ? "none" : `${ago} days ago`}</span></span>
                  : <>{pm(c.mean, c.sd)} <span class="muted">{c.pct_of_best !== undefined ? `${c.pct_of_best.toFixed(0)} % of your best` : ""}</span></>;
              })() : "—"}</dd>
            </dl>
            <details class="howto shape-details">
              <summary>Details: how the model scored it</summary>
              <dl class="kv">
                <dt title={KIND_HELP[s.kind]}>Evidence</dt><dd>{KIND_LABEL[s.kind]}</dd>
                <dt>Weight used</dt><dd>{s.weight === null ? "—" : s.weight.toFixed(2)} <span class="muted">{s.kind !== "implausible" ? `= ${(s.confidence * 100).toFixed(0)} % of a race's pull` : ""} · σ {s.sigma.toFixed(1)}</span></dd>
                {s.hr && <>
                  <dt title={s.hr.label ?? "with-HR model variant"}>With heart rate</dt>
                  <dd title={`${s.hr.label ?? ""}: ${KIND_HELP[s.hr.kind]}`}>{KIND_LABEL[s.hr.kind]}{s.hr.kind !== s.kind ? <span class="muted"> (was {KIND_LABEL[s.kind]})</span> : null}
                    <span class="muted">{s.hr.weight === null ? "" : ` · weight ${s.hr.weight.toFixed(2)}`}{s.hr.p_max !== null && s.hr.p_max !== undefined ? ` · ${Math.round(s.hr.p_max * 100)} % likely all-out` : ""}</span></dd>
                  {s.hr.note && <><dt>Heart-rate note</dt><dd class="muted" title={s.hr.note}>{s.hr.note}</dd></>}
                </>}
              </dl>
              <h3>Components</h3>
              <div class="tablewrap">
                <table>
                  <tbody>
                    {s.components_explained.filter((c) => !HR_KEYS.includes(c.key)).map((c) => (
                      <tr key={c.key}><td style={{ width: "150px" }}>{c.label}</td><td class="num" style={{ width: "110px" }}>{c.text}</td><td class="muted wrap">{c.meaning}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3>Heart rate (context only)</h3>
              {hrRows.length ? (
                <div class="tablewrap">
                  <table>
                    <tbody>
                      {hrRows.map((c, i) => (
                        <tr key={`${i}-${c.key}`}><td style={{ width: "150px" }}>{c.label}</td><td class="num" style={{ width: "110px" }}>{c.text}</td><td class="muted wrap">{c.meaning}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div class="empty">No usable heart rate in this session.</div>}
              <p class="note">Heart rate is not part of the performance score{data.hr_used_in_score ? " (the model file says it is: check fitness.json)" : ""}: as a direct correction it made race predictions worse,
                so the with-HR variant only uses it to judge whether a hard session was all-out.</p>
            </details>
          </div>
        );
      })}
      {!data.sessions.length && hrRows.length > 0 && (
        <details class="howto shape-details">
          <summary>Heart-rate details</summary>
          <div class="tablewrap"><table><tbody>
            {hrRows.map((c, i) => <tr key={i}><td style={{ width: "150px" }}>{c.label}</td><td class="num" style={{ width: "110px" }}>{c.text}</td><td class="muted wrap">{c.meaning}</td></tr>)}
          </tbody></table></div>
        </details>
      )}
    </section>
  );
}
