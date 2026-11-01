// Community · Wrapped: the club's year as a scroll of big cards (/api/community/wrapped/<year | latest>), like
// Spotify Wrapped: sessions, km, hours together, the busiest month, top place, favourite category, biggest day,
// best form jump, closest finish, most loyal, rivalry, records, badges, then one card per member.
//   /community/wrapped · /community/wrapped/<year>
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { useApi } from "../lib/api";
import { isoLocal, shortDate } from "../lib/format";
import { personColour, personName, setPeopleInfo } from "../lib/people";
import { useTheme } from "../lib/theme";
import { catInfo, catVar, delta, monthLabel, paceKm, plural, type Wrapped, type WrappedMember } from "../lib/community";
import { Av, AvStack } from "../components/CommunityBits";
import "./community.css";
import "./club-wrapped.css";

/** fades in (and counts numbers up) when scrolled into view */
function useSeen<T extends Element>(): [preact.RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect(); } }, { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

function Count({ v, d = 0, on }: { v: number; d?: number; on: boolean }) {
  const [x, setX] = useState(on ? v : 0);
  useEffect(() => {
    if (!on) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setX(v); return; }
    let raf = 0;
    const t0 = performance.now(), T = 900;
    const step = (t: number) => { const k = Math.min(1, (t - t0) / T); setX(v * (1 - Math.pow(1 - k, 3))); if (k < 1) raf = requestAnimationFrame(step); };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [v, on]);
  return <>{x.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d })}</>;
}

function Slide({ tone, kicker, children, cls, style }: { tone: string; kicker?: ComponentChildren; children: (seen: boolean) => ComponentChildren; cls?: string; style?: Record<string, string> }) {
  const [ref, seen] = useSeen<HTMLElement>();
  return (
    <section ref={ref} class={"wr-slide " + (cls ?? "") + (seen ? " in" : "")} style={{ "--t": tone, ...style } as any}>
      {kicker && <div class="wr-kicker">{kicker}</div>}
      {children(seen)}
    </section>
  );
}

const vsPrev = (v: number, p: number | undefined | null, unit: string, year: number) => {
  if (p === null || p === undefined) return null;
  if (!p) return <span class="wr-vs up">new this year</span>;
  const d = v - p;
  const r = Math.abs(d) >= 10 ? Math.round(Math.abs(d)).toLocaleString("en-GB") : Math.abs(d).toFixed(unit === "sessions" ? 0 : 1);
  return <span class={"wr-vs " + (d >= 0 ? "up" : "down")}>{d >= 0 ? "+" : "−"}{r} {unit} vs {year - 1}</span>;
};

function MemberSlide({ m, year, i }: { m: WrappedMember; year: number; i: number }) {
  const c = personColour(m.person);
  const cat = m.favourite_category ? catInfo(m.favourite_category) : null;
  return (
    <Slide tone={c} kicker={`${year} · member ${i + 1}`} cls="wr-member">
      {(seen) => <>
        <div class="wr-mhead"><Av p={m.person} size="xl" /><h2>{personName(m.person)}</h2></div>
        <div class="wr-big"><b><Count v={m.sessions} on={seen} /></b> sessions<span class="wr-sub">{Math.round(m.share * 100)} % of the club's year</span></div>
        <div class="wr-row">
          <div><b><Count v={m.km} d={0} on={seen} /></b><span>km</span></div>
          <div><b><Count v={m.hours} d={1} on={seen} /></b><span>hours</span></div>
          <div><b><Count v={m.reps} on={seen} /></b><span>reps</span></div>
          <div><b><Count v={m.climb_m} on={seen} /></b><span>m climbed</span></div>
        </div>
        <ul class="wr-facts">
          {m.best_session && <li><span class="k">Best day</span><a href={`/community/${m.best_session.gid}`}><b class={m.best_session.delta >= 0 ? "up" : "down"}>{delta(m.best_session.delta)}</b> vs form · {m.best_session.title} · {shortDate(m.best_session.date, "x")}</a></li>}
          {m.top_place && <li><span class="k">Home turf</span><b>{m.top_place}</b></li>}
          {cat && <li><span class="k">Favourite</span><b>{cat.emoji} {cat.label}</b></li>}
          {m.partner && <li><span class="k">Ran most with</span><span class="cm-pl"><Av p={m.partner.person} size="sm" />{personName(m.partner.person)} · {plural(m.partner.sessions, "session")}</span></li>}
          {m.company.text && <li><span class="k">Company</span><b>{m.company.text}</b>{m.company.thin ? <span class="muted small wr-tr">thin data: a hint, not a verdict</span> : null}</li>}
          {m.company.pace.filter((p) => Math.abs(p.diff_s_km) >= 1).slice(0, 1).map((p) => <li><span class="k">Rep pace</span><b>{p.text}</b><span class="muted small wr-tr">{p.label}: {paceKm(p.joint_s_km)} vs {paceKm(p.solo_s_km)} /km</span></li>)}
          {m.rivalry.map((r) => <li><span class="k">vs {personName(r.other)}</span><b>{r.won}–{r.lost}</b> in shared reps{r.trend ? <span class="muted wr-tr">{r.trend}</span> : null}</li>)}
          {m.streak_weeks >= 2 && <li><span class="k">Best streak</span><b>🔥 {m.streak_weeks} weeks</b></li>}
        </ul>
        {m.badges.length > 0 && <div class="wr-mbadges">{m.badges.map((b) => <a href={b.gid ? `/community/${b.gid}` : undefined} title={`${b.name}: ${b.description} (${b.date})`}><span>{b.emoji}</span>{b.name}</a>)}</div>}
      </>}
    </Slide>
  );
}

export function WrappedPage({ year }: { year: string | null }) {
  const { data: w, error, loading } = useApi<Wrapped>(`/api/community/wrapped/${year ?? "latest"}`);
  if (w) setPeopleInfo(w.people);
  useTheme();
  useEffect(() => { document.title = `Community · Wrapped ${w?.year ?? year ?? ""}`; }, [w, year]);
  if (error) return <main class="cm"><h1>Wrapped {year}</h1><p class="note">{error}</p><a href="/community/wrapped">Latest year ›</a></main>;
  if (!w) return <main class="cm wr"><p class="muted">{loading ? "Wrapping up the year…" : ""}</p></main>;
  const T = w.totals, P = w.previous, Y = w.year;
  const maxM = Math.max(1, ...w.months.map((m) => m.sessions));
  const people = w.members.map((m) => m.person);
  const r0 = w.rivalry[0];
  return (
    <main class="cm wr">
      <nav class="wr-years" aria-label="Year">
        {w.years.map((y) => <a class={y === String(Y) ? "on" : ""} href={`/community/wrapped/${y}`}>{y}</a>)}
      </nav>

      <Slide tone="var(--cat-intervals)" cls="wr-intro">
        {(seen) => <>
          <div class="wr-kicker">Club Wrapped</div>
          <div class="wr-year">{Y}</div>
          <div class="wr-lead"><b><Count v={T.sessions} on={seen} /></b> {T.sessions === 1 ? "session" : "sessions"} together · {plural(T.members, "member")}</div>
          <div class="wr-avs"><AvStack people={people} size="lg" max={10} /></div>
          <div class="wr-hint" aria-hidden="true">scroll ↓</div>
        </>}
      </Slide>

      <Slide tone="var(--accent)" kicker="The numbers">
        {(seen) => <>
          <div class="wr-big"><b><Count v={T.km} on={seen} /></b> km<span class="wr-sub">run, hiked and ridden together (all members added up)</span>{vsPrev(T.km, P?.km, "km", Y)}</div>
          <div class="wr-row">
            <div><b><Count v={T.hours} d={1} on={seen} /></b><span>hours moving</span>{vsPrev(T.hours, P?.hours, "h", Y)}</div>
            <div><b><Count v={T.person_hours_together} d={1} on={seen} /></b><span>person-hours out there together</span></div>
            <div><b><Count v={T.reps} on={seen} /></b><span>reps</span></div>
            <div><b><Count v={T.climb_m} on={seen} /></b><span>metres climbed</span></div>
            {T.kcal > 0 && <div><b><Count v={T.kcal} on={seen} /></b><span>kcal burned</span></div>}
          </div>
          {P && <div class="wr-foot">{vsPrev(T.sessions, P.sessions, "sessions", Y)}</div>}
        </>}
      </Slide>

      {w.busiest_month && (
        <Slide tone="var(--cat-tempo)" kicker="Month by month">
          {() => <>
            <div class="wr-big small"><b>{monthLabel(w.busiest_month!.month).split(" ")[0]}</b><span class="wr-sub">was the busiest month: {plural(w.busiest_month!.sessions, "session")} · {Math.round(w.busiest_month!.km)} km</span></div>
            <div class="wr-months">
              {w.months.map((m) => (
                <a class={"m" + (m.month === w.busiest_month!.month ? " top" : "")} href={m.sessions ? `/community?period=${m.month}` : undefined} title={`${monthLabel(m.month)}: ${plural(m.sessions, "session")} · ${m.km} km`}>
                  <i style={{ height: `${m.sessions ? 12 + (88 * m.sessions) / maxM : 3}%` }}>{m.sessions ? <span>{m.sessions}</span> : null}</i>
                  <span class="l">{monthLabel(m.month, true).slice(0, 3)}</span>
                </a>
              ))}
            </div>
          </>}
        </Slide>
      )}

      {w.top_place && (
        <Slide tone="var(--cat-long)" kicker="Home turf">
          {() => <>
            <div class="wr-huge">{w.top_place!.name}</div>
            <div class="wr-lead">{plural(w.top_place!.sessions, "session")} of {T.sessions} · {plural(T.places, "place")} in all</div>
            {w.top_place!.others.length > 0 && <div class="wr-chips">{w.top_place!.others.map((o) => <span>{o.name} <b>{o.sessions}</b></span>)}</div>}
          </>}
        </Slide>
      )}

      {w.favourite_category && (
        <Slide tone={catVar(w.favourite_category.id)} kicker="Your favourite kind of session">
          {() => <>
            <div class="wr-emoji" aria-hidden="true">{w.favourite_category!.emoji}</div>
            <div class="wr-huge">{w.favourite_category!.label}</div>
            <div class="wr-lead">{plural(w.favourite_category!.n, "session")} of {T.sessions}</div>
            <div class="wr-mix">{w.favourite_category!.mix.map((c) => <i style={{ flex: c.n, background: catVar(c.id) }} title={`${c.label}: ${c.n}`}><span>{c.emoji} {c.n}</span></i>)}</div>
          </>}
        </Slide>
      )}

      {w.biggest_day && (
        <Slide tone="var(--cat-easy)" kicker="The biggest day">
          {(seen) => <>
            <div class="wr-big"><b><Count v={w.biggest_day!.km} d={1} on={seen} /></b> km<span class="wr-sub">in one session, all of you together</span></div>
            <a class="wr-sess" href={`/community/${w.biggest_day!.gid}`}><b>{w.biggest_day!.title}</b> · {w.biggest_day!.place} · {shortDate(w.biggest_day!.date, "x")} <AvStack people={w.biggest_day!.people} size="sm" /></a>
            {w.longest && w.longest.gid !== w.biggest_day!.gid && (
              <div class="wr-also">And the longest together: <a href={`/community/${w.longest.gid}`}><b>{Math.floor(w.longest.minutes / 60)} h {w.longest.minutes % 60} min</b> · {w.longest.title} · {w.longest.place}</a>{w.longest.climb_m > 300 ? ` · ${w.longest.climb_m.toLocaleString("en-GB")} m climbed` : ""}</div>
            )}
          </>}
        </Slide>
      )}

      {w.form_jump && (
        <Slide tone={personColour(w.form_jump.person)} kicker="The best day of the year">
          {() => <>
            <div class="wr-huge num">{delta(w.form_jump!.delta)}</div>
            <div class="wr-lead"><Av p={w.form_jump!.person} size="lg" /> <b>{personName(w.form_jump!.person)}</b> ran {delta(w.form_jump!.delta)} VDOT above their form</div>
            <a class="wr-sess" href={`/community/${w.form_jump!.gid}`}><b>{w.form_jump!.title}</b> · {w.form_jump!.place} · {shortDate(w.form_jump!.date, "x")}</a>
          </>}
        </Slide>
      )}

      {w.closest_finish && (
        <Slide tone="var(--cat-race)" kicker="The closest finish">
          {() => <>
            <div class="wr-huge num">{w.closest_finish!.value.toFixed(1)} s</div>
            <div class="wr-lead">{w.closest_finish!.text}</div>
            <a class="wr-sess" href={`/community/${w.closest_finish!.gid}`}>{shortDate(w.closest_finish!.date, "x")} · open the session ›</a>
          </>}
        </Slide>
      )}

      {w.most_loyal && (
        <Slide tone={personColour(w.most_loyal.person)} kicker="Always there">
          {() => <>
            <div class="wr-mhead center"><Av p={w.most_loyal!.person} size="xl" /></div>
            <div class="wr-huge">{personName(w.most_loyal!.person)}</div>
            <div class="wr-lead">showed up to <b>{w.most_loyal!.sessions}</b> of {w.most_loyal!.of} sessions{w.most_loyal!.sessions === w.most_loyal!.of ? " — every single one" : ""}</div>
            {w.top_pair && <div class="wr-also">Strongest pair: <Av p={w.top_pair.a} size="sm" /> {personName(w.top_pair.a)} & <Av p={w.top_pair.b} size="sm" /> {personName(w.top_pair.b)} · {plural(w.top_pair.sessions, "session")} together</div>}
          </>}
        </Slide>
      )}

      {r0 && (
        <Slide tone="var(--cat-intervals)" kicker="The rivalry">
          {() => <>
            <div class="wr-duel">
              <span><Av p={r0.a} size="xl" /><b>{personName(r0.a)}</b></span>
              <b class="sc">{r0.wins[r0.a]}<i>–</i>{r0.wins[r0.b]}</b>
              <span><Av p={r0.b} size="xl" /><b>{personName(r0.b)}</b></span>
            </div>
            <div class="wr-lead">shared reps won over {plural(r0.sessions, "session")}{r0.ties ? ` · ${plural(r0.ties, "tie")}` : ""}</div>
            {r0.trend && <div class="wr-trend">{r0.trend.text}</div>}
            {w.rivalry.slice(1, 4).map((r) => <div class="wr-also">{r.headline}</div>)}
          </>}
        </Slide>
      )}

      {(w.records_set.length > 0 || w.records_standing.length > 0) && (
        <Slide tone="var(--cat-race)" kicker="Records">
          {() => <>
            <div class="wr-big small"><b>{w.records_standing.length || w.records_set.length}</b> {w.records_standing.length ? "club records set this year still stand" : "records set"}</div>
            <ul class="wr-recs">
              {(w.records_standing.length ? w.records_standing.map((r) => ({ gid: r.gid, person: r.person, text: `${r.label} ${r.measure === "race" ? "race" : r.measure === "avg" ? "average rep" : "best rep"} ${r.text}`, date: r.date }))
                : w.records_set.map((r) => ({ gid: r.gid, person: r.person, text: r.text, date: r.date }))).slice(0, 8).map((r) => (
                <li><a href={`/community/${r.gid}`}><span>🏆</span><Av p={r.person} size="sm" /><b>{r.text}</b><span class="muted"> · {shortDate(r.date, "x")}</span></a></li>
              ))}
            </ul>
          </>}
        </Slide>
      )}

      {w.badges.length > 0 && (
        <Slide tone="var(--cat-race)" kicker="Badges earned">
          {(seen) => <>
            <div class="wr-big small"><b><Count v={w.badges.length} on={seen} /></b> badges this year</div>
            <div class="wr-badges">
              {w.badges.map((b) => (
                <a href={b.gid ? `/community/${b.gid}` : undefined} title={`${b.description}${b.detail ? ` · ${b.detail}` : ""}`}>
                  <span class="e">{b.emoji}</span><b>{b.name}</b><span class="muted"><Av p={b.person!} size="xs" /> {personName(b.person!)} · {shortDate(b.date!, isoLocal())}</span>
                </a>
              ))}
            </div>
          </>}
        </Slide>
      )}

      {w.members.map((m, i) => <MemberSlide key={m.person} m={m} year={Y} i={i} />)}

      <Slide tone="var(--cat-easy)" cls="wr-outro">
        {() => <>
          <div class="wr-kicker">That was {Y}</div>
          <div class="wr-huge">See you on the track in {Y + 1} 👟</div>
          {w.first && w.last && <div class="wr-lead">From {w.first.title} ({shortDate(w.first.date, "x")}) to {w.last.title} ({shortDate(w.last.date, "x")})</div>}
          <div class="wr-links"><a href="/community/club">Club ›</a><a href="/community">All sessions ›</a>{w.years.filter((y) => Number(y) < Y).slice(0, 1).map((y) => <a href={`/community/wrapped/${y}`}>{y} Wrapped ›</a>)}</div>
        </>}
      </Slide>
    </main>
  );
}
