// Community · Club: the club dashboard (/api/community/club?from=&to=). Totals (period + this year), the month chart,
// highlights, the attendance leaderboard (sessions, share, streaks, last seen, avg form delta), pairs, category mix,
// club records, a weekday × hour heatmap, places and the newest sessions.
//   /community/club?period=2026|12m|90d
import { useEffect, useMemo } from "preact/hooks";
import { qs, useApi } from "../lib/api";
import { setQuery, useLocation } from "../lib/router";
import { isoLocal, shortDate } from "../lib/format";
import { personColour, personName, setPeopleInfo } from "../lib/people";
import { useTheme } from "../lib/theme";
import { repTime } from "../lib/together";
import { ago, catVar, CATS, delta, HIGHLIGHT, hrs, kms, MEASURE_LABEL, periods, plural, resolvePeriod, type Club } from "../lib/community";
import { PageHeader } from "../components/PageHeader";
import { Av, AvStack, CatChip, FormChip, PairCard, PersonLink, Stat } from "../components/CommunityBits";
import { ClubMonthChart } from "../components/ClubCharts";
import { CompanyCard, GoalCard, LatestBadges, RivalryCard } from "../components/ClubMotivation";
import "./community.css";

const MEDAL = ["🥇", "🥈", "🥉"];
const recTime = (r: { measure: string; value_s: number }) => (r.measure === "race" ? repTime(r.value_s).replace(/\.\d$/, "") : repTime(r.value_s));

function PeriodSelect({ period, years }: { period: string; years: string[] }) {
  const per = resolvePeriod(period);
  return (
    <select value={period} onChange={(e) => setQuery({ period: (e.target as HTMLSelectElement).value })} aria-label="Period">
      <option value="">All time</option>
      {per && !periods(years).some((p) => p.id === period) && <option value={period}>{per.label}</option>}
      {periods(years).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
    </select>
  );
}

function Heatmap({ h }: { h: Club["heatmap"] }) {
  useTheme();
  const max = Math.max(1, ...h.n.flat());
  // show the hours that have sessions, padded to at least 6–22
  const used = h.hours.filter((hr) => h.n.some((row) => row[hr] > 0));
  const lo = Math.min(6, ...used), hi = Math.max(22, ...used);
  const hours = h.hours.filter((x) => x >= lo && x <= hi);
  return (
    <div class="cm-heat" style={{ "--cols": hours.length } as any}>
      <span />
      {hours.map((x) => <span class="hh">{x % 3 === 0 ? String(x).padStart(2, "0") : ""}</span>)}
      {h.weekdays.map((wd, i) => <>
        <span class="wd">{wd}</span>
        {hours.map((x) => {
          const n = h.n[i][x];
          return <i class={n ? "on" : ""} style={n ? { opacity: 0.25 + 0.75 * (n / max) } : undefined} title={`${wd} ${String(x).padStart(2, "0")}:00 · ${plural(n, "session")}`} />;
        })}
      </>)}
    </div>
  );
}

export function ClubPage() {
  const { query } = useLocation();
  const period = query.get("period") ?? "";
  const per = resolvePeriod(period);
  const allTime = useApi<Club>("/api/community/club");
  const { data, error, loading } = useApi<Club>("/api/community/club" + qs({ from: per?.from, to: per?.to }));
  if (data) setPeopleInfo(data.people);
  useEffect(() => { document.title = "Community · Club"; }, []);
  useTheme();

  const years = useMemo(() => {
    const t = allTime.data?.totals;
    if (!t?.first || !t.last) return [];
    const out: string[] = [];
    for (let y = Number(t.last.slice(0, 4)); y >= Number(t.first.slice(0, 4)); y--) out.push(String(y));
    return out;
  }, [allTime.data]);

  const c = data;
  const t = c?.totals;
  // month chart: all time = the last 36 months when longer (a period shows all its months)
  const months = useMemo(() => {
    const m = c?.months ?? [];
    return !per && m.length > 36 ? m.slice(-36) : m;
  }, [c, per]);
  const catTotal = (c?.categories ?? []).reduce((s, x) => s + x.n, 0);
  const placeMax = Math.max(1, ...(c?.places ?? []).map((p) => p.sessions));
  const pm = c?.pair_matrix;
  const pmMax = Math.max(1, ...(pm?.n.flat() ?? [1]));

  return (
    <main class="cm">
      <PageHeader title="Club" sub={t ? `${plural(t.sessions, "session")} · ${plural(t.members, "member")}${per ? ` · ${per.label}` : ""}` : loading ? "Loading…" : ""}
        about="The running club at a glance: how much you run together, who turns up, the club records and the best moments."
        tips={[
          "Streak = consecutive weeks with a club session; it stays alive while the last one is this or last week",
          "Avg form = the mean of a member's form deltas (session score minus shape the day before)",
          "Club records only count community sessions (shared reps everyone ran, races), not each member's solo training",
          "Click a month in the chart to list its sessions; a highlight or record opens its session",
        ]}>
        <PeriodSelect period={period} years={years} />
      </PageHeader>
      {error && <div class="err">{error}</div>}
      {c && t && <>
        <div class="cm-hero">
          <div class="cm-hero-main">
            <span class="lbl">{per ? per.label : "All time"}</span>
            <div class="big"><b>{t.sessions}</b> sessions together</div>
            <div class="cm-stats">
              <Stat v={Math.round(t.km).toLocaleString("en-GB")} label="km" sub="all members" />
              <Stat v={t.hours.toFixed(t.hours >= 100 ? 0 : 1)} label="hours" sub="moving" />
              <Stat v={t.person_hours_shared.toFixed(0)} label="person-hours" sub="out there together" title={`${t.person_hours_together.toFixed(1)} person-hours side by side (within ~50 m)`} />
              <Stat v={t.reps} label="reps" />
              <Stat v={t.climb_m.toLocaleString("en-GB")} label="m climbed" />
              <Stat v={t.places} label="places" />
              {t.guest_visits > 0 && <Stat v={t.guest_visits} label="guest visits" />}
              {t.kudos > 0 && <Stat v={t.kudos} label="kudos" />}
            </div>
            {t.first && <div class="muted small">{shortDate(t.first, isoLocal())} – {t.last ? shortDate(t.last, isoLocal()) : ""} · {plural(t.members, "member")}{t.guests ? ` · ${plural(t.guests, "guest")}` : ""}</div>}
          </div>
          {!per && t.this_year && (
            <div class="cm-hero-year">
              <span class="lbl">This year · {t.this_year.year}</span>
              <div class="big"><b>{t.this_year.sessions}</b> sessions</div>
              <div class="cm-stats"><Stat v={Math.round(t.this_year.km)} label="km" /><Stat v={t.this_year.hours.toFixed(1)} label="hours" /></div>
              {c.most_loyal && <div class="loyal"><Av p={c.most_loyal.person} size="sm" /><span>Most loyal: <b>{personName(c.most_loyal.person)}</b> · {plural(c.most_loyal.sessions, "session")}</span></div>}
              {t.this_year.sessions > 0 && <a class="cm-wrapped-link" href={`/community/wrapped/${t.this_year.year}`}>✨ {t.this_year.year} Wrapped ›</a>}
            </div>
          )}
        </div>

        <GoalCard initial={c.goal} />

        {c.latest_badges.length > 0 && (
          <section class="cm-panel cm-badges-strip">
            <div class="head"><h2>Latest badges</h2><span class="sub">earned in club sessions · click one for the session</span><a class="sub" href="/community/members">all members ›</a></div>
            <LatestBadges badges={c.latest_badges} />
          </section>
        )}

        <div class="cm-grid">
          {Object.keys(c.company).length > 0 && (
            <section class="cm-panel span2">
              <div class="head"><h2>Faster together than alone?</h2><span class="sub">form delta in club sessions vs solo sessions of the same kind (±60 days){per ? ` · ${per.label}` : ""}</span></div>
              <div class="cc-grid">{c.attendance.filter((a) => c.company[a.person]).map((a) => <CompanyCard p={a.person} c={c.company[a.person]} />)}</div>
              <div class="cc-note">Form delta = the session's score minus the shape the day before (VDOT points). Rep pace compares interval sessions with reps of similar length (short &lt; 600 m, medium, long ≥ 1.4 km) within ±90 days.</div>
            </section>
          )}

          {c.rivalry.length > 0 && (
            <section class="cm-panel span2">
              <div class="head"><h2>Rivalry</h2><span class="sub">head-to-head over time: shared reps and races</span></div>
              <div class="cr-list">{c.rivalry.slice(0, 6).map((r) => <RivalryCard key={r.a + r.b} r={r} viewer={c.viewer} />)}</div>
            </section>
          )}

          <section class="cm-panel span2">
            <div class="head"><h2>Month by month</h2><span class="sub">{!per && (c.months.length > 36) ? "last 36 months" : ""}</span></div>
            <ClubMonthChart months={months} />
          </section>

          {c.highlights.length > 0 && (
            <section class="cm-panel span2">
              <div class="head"><h2>Highlights</h2><span class="sub">the moments to remember</span></div>
              <div class="cm-highs">
                {c.highlights.map((h) => {
                  const k = HIGHLIGHT[h.kind] ?? { emoji: "⭐", label: h.kind };
                  return (
                    <a class="cm-high" href={`/community/${h.gid}`} style={h.person ? ({ "--c": personColour(h.person) } as any) : undefined}>
                      <span class="e" aria-hidden="true">{k.emoji}</span>
                      <span class="k">{k.label}</span>
                      <b class="v">{h.unit === "VDOT" ? delta(h.value) : h.value.toLocaleString("en-GB")}<small> {h.unit === "VDOT" ? "vs form" : h.unit}</small></b>
                      <span class="t">{h.text}</span>
                      <span class="d muted">{shortDate(h.date, isoLocal())}{h.person && <> · <Av p={h.person} size="xs" />{h.other && <Av p={h.other} size="xs" />}</>}</span>
                    </a>
                  );
                })}
              </div>
            </section>
          )}

          <section class="cm-panel span2">
            <div class="head"><h2>Attendance</h2><span class="sub">who turns up</span></div>
            <div class="tablewrap cm-tw">
              <table class="cm-tbl">
                <thead><tr><th /><th>Member</th><th class="num">Sessions</th><th>Share</th><th class="num" title="Consecutive weeks with a club session (alive = this or last week)">Streak</th>
                  <th class="num">Best streak</th><th>Last seen</th><th class="num" title="Mean form delta over the sessions with a score">Avg form</th><th class="num">km</th><th class="num">Hours</th><th class="num">Reps</th><th>Mostly</th><th>Favourite place</th></tr></thead>
                <tbody>
                  {c.attendance.map((a, i) => (
                    <tr>
                      <td class="rank">{MEDAL[i] ?? i + 1}</td>
                      <td><PersonLink p={a.person} /></td>
                      <td class="num"><b>{a.sessions}</b></td>
                      <td><span class="cm-share"><i style={{ width: `${Math.round(a.share * 100)}%`, background: personColour(a.person) }} /></span><span class="muted small"> {Math.round(a.share * 100)} %</span></td>
                      <td class="num">{a.streak_weeks > 0 ? <span class="streak" title={`${a.streak_weeks} weeks in a row`}>🔥 {a.streak_weeks} wk</span> : <span class="muted">—</span>}</td>
                      <td class="num">{a.best_streak_weeks} wk</td>
                      <td>{a.last_gid ? <a href={`/community/${a.last_gid}`}>{ago(a.days_since)}</a> : "—"}</td>
                      <td class="num"><FormChip v={a.avg_form_delta} showNone info={{ delta: a.avg_form_delta, text: `average over ${plural(a.n_form, "session")} with a score` }} /></td>
                      <td class="num">{Math.round(a.km)}</td>
                      <td class="num">{a.hours.toFixed(1)}</td>
                      <td class="num">{a.reps}</td>
                      <td><span class="cm-mini-mix">{CATS.map((k) => a.by_category[k.id] ? <i style={{ flex: a.by_category[k.id], background: catVar(k.id) }} title={`${k.label}: ${a.by_category[k.id]}`} /> : null)}</span></td>
                      <td class="muted">{a.favourite_place ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section class="cm-panel">
            <div class="head"><h2>Category mix</h2></div>
            <div class="cm-mix big">{c.categories.filter((k) => k.n).map((k) => <i style={{ flex: k.n, background: catVar(k.id) }} title={`${k.label}: ${k.n}`} />)}</div>
            <table class="cm-tbl plain">
              <tbody>
                {c.categories.filter((k) => k.n).sort((a, b) => b.n - a.n).map((k) => (
                  <tr><td><CatChip id={k.id} label={k.label} /></td><td class="num"><b>{k.n}</b></td><td class="num muted">{catTotal ? Math.round((100 * k.n) / catTotal) : 0} %</td><td class="num">{kms(k.km)}</td><td class="num muted">{hrs(k.hours)}</td></tr>
                ))}
              </tbody>
            </table>
          </section>

          <section class="cm-panel">
            <div class="head"><h2>Pairs</h2><span class="sub">sessions together</span></div>
            {pm && pm.people.length > 1 && (
              <table class="cm-matrix">
                <thead><tr><th />{pm.people.map((p) => <th><Av p={p} size="sm" /></th>)}</tr></thead>
                <tbody>{pm.people.map((p, i) => (
                  <tr><th><Av p={p} size="sm" /></th>{pm.n[i].map((n, j) => (
                    <td class={i === j ? "diag" : ""} style={i !== j && n ? { background: `color-mix(in srgb, var(--accent) ${Math.round(12 + 60 * (n / pmMax))}%, transparent)` } : undefined}
                      title={i === j ? `${personName(p)}: ${plural(n, "session")}` : `${personName(p)} & ${personName(pm.people[j])}: ${plural(n, "session")} together`}>{n || ""}</td>))}</tr>
                ))}</tbody>
              </table>
            )}
            <div class="cm-pairs">{c.pairs.slice(0, 4).map((p) => <PairCard key={p.a + p.b} h={p.h2h} viewer={c.viewer} sessions={p.sessions} />)}</div>
          </section>

          <section class="cm-panel span2">
            <div class="head"><h2>Club records</h2><span class="sub">fastest in a community session · {plural(c.records.length, "record")}</span></div>
            {c.records.length ? (
              <div class="tablewrap cm-tw">
                <table class="cm-tbl">
                  <thead><tr><th>Distance</th><th>Kind</th><th class="num">Time</th><th>Holder</th><th>Set</th><th>Everyone's best</th><th class="num">Efforts</th></tr></thead>
                  <tbody>
                    {[...c.records].sort((a, b) => (a.measure === "race" ? 0 : 1) - (b.measure === "race" ? 0 : 1) || a.dist_m - b.dist_m || a.measure.localeCompare(b.measure)).map((r) => (
                      <tr>
                        <td><b>{r.label}</b></td>
                        <td class="muted">{MEASURE_LABEL[r.measure] ?? r.measure}</td>
                        <td class="num"><b class="rec">🏆 {recTime(r)}</b></td>
                        <td><PersonLink p={r.person} /></td>
                        <td><a href={`/community/${r.gid}`}>{shortDate(r.date, isoLocal())}</a></td>
                        <td class="small">{Object.entries(r.by_person).sort((a, b) => a[1].value_s - b[1].value_s).map(([p, v]) => (
                          <a class="cm-pb" href={`/community/${v.gid}`} title={`${personName(p)} · ${shortDate(v.date, isoLocal())}`}><Av p={p} size="xs" />{recTime({ measure: r.measure, value_s: v.value_s })}</a>
                        ))}</td>
                        <td class="num muted">{r.n_efforts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <div class="empty">No records yet: a record needs a rep distance everyone ran, or a race.</div>}
          </section>

          <section class="cm-panel">
            <div class="head"><h2>When we meet</h2><span class="sub">weekday × start hour</span></div>
            <Heatmap h={c.heatmap} />
          </section>

          <section class="cm-panel">
            <div class="head"><h2>Places</h2><span class="sub">{plural(c.places.length, "place")}</span></div>
            <div class="cm-places">
              {[...c.places].sort((a, b) => b.sessions - a.sessions || b.last.localeCompare(a.last)).slice(0, 10).map((p) => (
                <a class="cm-place" href={`/community?q=${encodeURIComponent(p.name)}`} title={`Last: ${p.last}`}>
                  <span class="nm">{p.name}</span>
                  <span class="bar"><i style={{ width: `${(100 * p.sessions) / placeMax}%` }} /></span>
                  <span class="num">{p.sessions}</span>
                </a>
              ))}
            </div>
          </section>

          <section class="cm-panel span2">
            <div class="head"><h2>Recent sessions</h2><a class="sub" href="/community">all sessions ›</a></div>
            <div class="cm-recent">
              {c.recent.map((r) => (
                <a class="cm-rc" href={`/community/${r.gid}`} style={{ "--c": catVar(r.category) } as any}>
                  <CatChip id={r.category} />
                  <b class="w">{r.title}</b>
                  <span class="muted">{shortDate(r.date, isoLocal())}</span>
                  <AvStack people={r.people} size="sm" />
                  <span class="num">{kms(r.km)}</span>
                  {r.records > 0 && <span title="records set">🏆 {r.records}</span>}
                </a>
              ))}
            </div>
          </section>

          {c.guests.length > 0 && (
            <section class="cm-panel">
              <div class="head"><h2>Guests</h2></div>
              <div class="chips">{c.guests.map((g) => <span class="chip">{g.name} <span class="n">{g.visits}</span></span>)}</div>
            </section>
          )}
        </div>
      </>}
    </main>
  );
}
