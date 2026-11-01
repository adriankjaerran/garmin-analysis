// Motivation bits of a club session page: the story (the session in a few sentences), the highlights strip
// ("Rep of the day" = the closest rep, fastest rep, faster recovery, strongest finish, most even, biggest form jump;
// for runs together / races: side by side, winner, most in front, fastest km) and "Last time here": the previous
// session at the same place (the same workout if there is one, else the same category) with each member's fitness
// score then and now (+ average rep for the same workout). Facts: lib/communitySession.ts sessionFacts().
import { setQuery } from "../lib/router";
import { shortDate } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { repTime } from "../lib/together";
import { useCommunity, type CsSession, type Facts } from "../lib/communitySession";
import { Av } from "./CommunityBits";
import "../pages/community-session.css";

export function SessionStory({ f }: { f: Facts }) {
  if (!f.story.length) return null;
  return <p class="cs-story"><span class="cs-story-q" aria-hidden="true">“</span>{f.story.join(" ")}</p>;
}

export function SessionHighlights({ f }: { f: Facts }) {
  useTheme();
  if (!f.highlights.length) return null;
  const go = (rep: string) => {
    setQuery({ rep });
    setTimeout(() => document.querySelector(".tg-rb-row.selected, .tg-tbl.mx tr.selected")?.scrollIntoView({ block: "center", behavior: "smooth" }), 50);
  };
  return (
    <div class="cs-hls">
      {f.highlights.map((h) => {
        const body = <>
          <span class="cs-hl-e" aria-hidden="true">{h.emoji}</span>
          <span class="cs-hl-t">{h.title}</span>
          <b>{h.value}</b>
          {h.sub && <small>{h.sub}</small>}
        </>;
        const style = h.person ? ({ "--pc": personColour(h.person) } as any) : undefined;
        return h.rep
          ? <button key={h.id} class={"cs-hl" + (h.person ? " p" : "")} style={style} onClick={() => go(h.rep!)} title={`Show Rep ${h.rep} in the table and the close-up`}>{body}</button>
          : <div key={h.id} class={"cs-hl" + (h.person ? " p" : "")} style={style}>{body}</div>;
      })}
    </div>
  );
}

/** the previous session at this place to compare with: the same workout if any, else the same category */
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
        <span class="cs-sp-date">{shortDate(x.date, c.date)}</span><span class="cs-sp-wk">{x.workout}</span><span aria-hidden="true">›</span>
      </a>
      {!p ? <div class="muted small">Loading…</div> : (
        <ul class="cs-last">
          {rows.map(({ m, o, now, then, unrel, d, rep }) => (
            <li key={m.key}>
              <span class="pn"><Av p={m.key} size="sm" /><b>{personName(m.key)}</b></span>
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
    <div class="card cs-lastcard">
      <div class="head sm"><h2>Last time here</h2>
        <span class="sub">{lt.same ? `the same workout at ${c.place?.name ?? "this place"}` : `the last ${c.category_info.label.toLowerCase()} session at ${c.place?.name ?? "this place"}`} · score = fitness (VDOT scale)</span></div>
      <LastTimeBody c={c} gid={lt.x.gid} same={lt.same} base={base} />
    </div>
  );
}
