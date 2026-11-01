// "Last time here" on a club session page: the previous session at the same place (the same workout if there is
// one, else the same type) with each member's fitness score then and now (+ average rep for the same workout).
import { shortDate } from "../lib/format";
import { personName } from "../lib/people";
import { repTime } from "../lib/together";
import { useCommunity, type CsSession } from "../lib/communitySession";

/** the previous session at this place to compare with: the same workout if any, else the same type */
export function lastTimeHere(c: CsSession) {
  const older = c.same_place.filter((x) => x.date < c.date || (x.date === c.date && x.gid < c.gid));
  const same = older.find((x) => x.workout === c.workout);
  if (same) return { x: same, same: true };
  const cat = older.find((x) => x.category === c.category_info.id);
  return cat ? { x: cat, same: false } : null;
}

function LastTimeBody({ c, gid, same, base }: { c: CsSession; gid: string; same: boolean; base: string }) {
  const { data: p } = useCommunity(gid);
  const x = c.same_place.find((y) => y.gid === gid)!;
  const rows = c.members.filter((m) => m.role !== "other").map((m) => {
    const o = p?.members.find((y) => y.person === m.person && y.role !== "other");
    // scores the fitness model marks unreliable (confidence "none") are hidden, as everywhere else
    const ok = (f: typeof m.form | undefined) => (f && f.confidence !== "none" ? f.score ?? null : null);
    const now = ok(m.form), then = ok(o?.form);
    const unrel = (m.form?.score != null && now === null) || (o?.form?.score != null && then === null);
    const comparable = now !== null && then !== null && m.form?.discipline === o?.form?.discipline;
    return { m, o, now, then, unrel, d: comparable ? now! - then! : null, rep: same && m.avg_s && o?.avg_s ? m.avg_s - o.avg_s : null };
  });
  return (
    <>
      <a class="cs-last-link" href={`${base}/${encodeURIComponent(gid)}`}>
        {shortDate(x.date, c.date)} · {x.workout}
      </a>
      {!p ? <div class="muted small">Loading…</div> : (
        <ul class="cs-last">
          {rows.map(({ m, o, now, then, unrel, d, rep }) => (
            <li key={m.key}>
              <span class="pn">{personName(m.key)}:</span>
              {!o ? <span class="muted small">wasn't there</span> : (
                <span class="cs-last-v">
                  {d !== null
                    ? <span title={`Fitness score (VDOT scale, ${m.form?.discipline ?? ""}): ${then!.toFixed(1)} then, ${now!.toFixed(1)} now`}>
                        score {then!.toFixed(1)} → <b>{now!.toFixed(1)}</b> <span class={d >= 0.3 ? "good" : d <= -0.3 ? "bad" : "muted"}>{d > 0 ? "+" : d < 0 ? "−" : "±"}{Math.abs(d).toFixed(1)}</span></span>
                    : <span class="muted small" title={unrel ? "The fitness model marks a score here as unreliable, so it isn't compared" : "A fitness score needs a hard session with heart rate on both days"}>{now === null && then === null ? "no scores" : now === null ? (unrel ? "score today unreliable" : "no score today") : unrel ? "score then unreliable" : "no score then"}</span>}
                  {rep !== null && <span title="Average time per rep (every full rep each ran), then vs now"> · avg rep {repTime(o.avg_s)} → <b>{repTime(m.avg_s)}</b> <span class={rep < -0.2 ? "good" : rep > 0.2 ? "bad" : "muted"}>{rep < 0 ? "−" : "+"}{Math.abs(rep).toFixed(1)} s</span></span>}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function LastTimeHere({ c, base }: { c: CsSession; base: string }) {
  const lt = lastTimeHere(c);
  if (!lt) return null;
  return (
    <section class="cs-lastcard">
      <h2>Last time here <span class="muted small">· {lt.same ? `the same workout at ${c.place?.name ?? "this place"}` : `the last ${c.category_info.label.toLowerCase()} session at ${c.place?.name ?? "this place"}`} · score = fitness (VDOT scale)</span></h2>
      <LastTimeBody c={c} gid={lt.x.gid} same={lt.same} base={base} />
    </section>
  );
}
