// Activity page "Shape" section: what this session is worth in the fitness model (score, explanation,
// components, shape that day) plus HR diagnostics, which are shown but not used in the score.
import { useApi } from "../lib/api";
import { DISC_LABEL, KIND_HELP, KIND_LABEL, discColor, pm, raceT, signed, unitShort, type FitnessSession } from "../lib/fitness";
import "../fitness.css";

const HR_KEYS = ["work_hrr", "hrr"];
const SHORT: Record<string, string> = { race: "counts fully", hard: "full weight unless far below the shape",
  bound: "lower bound: only counts when above the shape", implausible: "ignored" };

/** `not_scored` (WP-A): why the model skipped this activity */
type SessionResp = FitnessSession & { not_scored?: { key: string; text: string } | null };

export function ShapeSection({ id }: { id: string }) {
  const { data } = useApi<SessionResp>(`/api/fitness/session/${id}`);
  // no fitness.json, or nothing to say (unscored sport without HR: a swim, a walk) -> no section
  if (!data || (!data.scored && !data.hr_diagnostics)) return null;
  const hd = data.hr_diagnostics;
  // HR from the scored part (work bouts / best window) first, then the steady-chunk diagnostics
  const hrRows = [
    ...data.sessions.flatMap((s) => s.components_explained.filter((c) => HR_KEYS.includes(c.key)).map((c) => ({
      ...c, label: c.key === "work_hrr" ? "Work effort (%HRR)" : "Window effort (%HRR)",
      meaning: c.key === "work_hrr" ? "mean HR during the reps, % of heart-rate reserve" : `mean HR over the scored ${DISC_LABEL[s.discipline].toLowerCase()} window, % of HR reserve` }))).slice(0, 1),
    ...(hd?.explained.filter((c) => c.text !== "—") ?? []),
  ];
  return (
    <section>
      <div class="head">
        <h2>Shape</h2>
        <span class="sub">what this session says about fitness (<a href="/fitness">Fitness</a> page)</span>
      </div>
      <div class="cols" style={{ gridTemplateColumns: "3fr 2fr" }}>
        <div>
          {!data.scored && <div class="empty">Not scored by the fitness model: {data.not_scored?.text ?? "only interval, race, run and ski sessions with a watch file are."}</div>}
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
                  <dt>Discipline</dt><dd>{s.label}</dd>
                  <dt>Score</dt><dd><b>{s.score.toFixed(1)}</b> {unitShort(s.discipline)} <span class="muted">σ {s.sigma.toFixed(1)}</span></dd>
                  <dt>Evidence</dt><dd title={KIND_HELP[s.kind]}>{KIND_LABEL[s.kind]} <span class="muted">{SHORT[s.kind]}</span></dd>
                  <dt>Weight used</dt><dd>{s.weight === null ? "—" : s.weight.toFixed(2)} <span class="muted">{s.kind !== "implausible" ? `= ${(s.confidence * 100).toFixed(0)} % of a race's pull` : ""}</span></dd>
                  <dt>Shape that day</dt><dd>{sh ? <>{pm(s.shape_at ?? sh.mean, sh.sd)}{s.shape_race_equivalents_s ? <span class="muted"> · 5k {raceT(s.shape_race_equivalents_s["5k"])}</span> : null}</> : "—"}</dd>
                  <dt>Score − shape</dt><dd>{delta === null ? "—" : signed(delta)}</dd>
                  <dt>Shape now</dt><dd>{s.current ? <>{pm(s.current.mean, s.current.sd)} <span class="muted">{s.current.pct_of_best !== undefined ? `${s.current.pct_of_best.toFixed(1)} % of best ${s.personal_best?.toFixed(1)}` : ""}</span></> : "—"}</dd>
                  <dt>Race equivalents</dt><dd>{eq ? `5k ${raceT(eq["5k"])} · 10k ${raceT(eq["10k"])} · half ${raceT(eq.half)}` : <span class="muted">{s.discipline === "ski" ? "n/a (ski scale)" : "—"}</span>}</dd>
                </dl>
                <h3>Components</h3>
                <div class="tablewrap">
                  <table>
                    <tbody>
                      {s.components_explained.filter((c) => !HR_KEYS.includes(c.key)).map((c) => (
                        <tr key={c.key}><td style={{ width: "140px" }}>{c.label}</td><td class="num" style={{ width: "110px" }}>{c.text}</td><td class="muted wrap">{c.meaning}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
        <div>
          <h3 class="muted" style={{ margin: "0 0 var(--space-1)", fontSize: "var(--fs-13)" }}>Heart-rate diagnostics</h3>
          {hrRows.length ? (
            <div class="tablewrap">
              <table>
                <tbody>
                  {hrRows.map((c, i) => (
                    <tr key={`${i}-${c.key}`}><td style={{ width: "150px" }}>{c.label}</td><td class="num" style={{ width: "90px" }}>{c.text}</td><td class="muted wrap" style={{ whiteSpace: "normal" }}>{c.meaning}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div class="empty">No usable HR in this session.</div>}
          <p class="note"><span class="dot none" />HR is <b>not used in the score</b>{data.hr_used_in_score ? " (the model says it is: check fitness.json)" : ""}. In the evaluation it made race
            prediction worse (MAE 1.86 → 2.37 VDOT): the wrist sensor under-reads hard intervals erratically and the watch change shifted HR.
            These numbers are for context only.</p>
        </div>
      </div>
    </section>
  );
}
