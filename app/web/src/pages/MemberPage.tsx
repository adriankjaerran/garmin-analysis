// Community · Members: a card per member (/api/community/members) and a member's profile
// (/api/community/members/<person>): totals, categories, form delta per session, shape trend, training partners
// (sessions together), best sessions and every session.
//   /community/members · /community/members/<person>
import { useEffect } from "preact/hooks";
import { useApi } from "../lib/api";
import { dur, isoLocal, shortDate } from "../lib/format";
import { personColour, personName, setPeopleInfo, type PersonInfo } from "../lib/people";
import { useTheme } from "../lib/theme";
import { DISC_LABEL } from "../lib/fitness";
import { ago, catVar, CATS, delta, hrs, kms, plural, type Attendance, type Member, type MemberSession } from "../lib/community";
import { PageHeader } from "../components/PageHeader";
import { Av, CatChip, FormChip, PersonLink, Stat } from "../components/CommunityBits";
import { ClubFormChart, ClubShapeChart } from "../components/ClubCharts";
import { BadgeGrid, CompanyCard } from "../components/ClubMotivation";
import "./community.css";

const CatBars = ({ by, total }: { by: Attendance["by_category"]; total: number }) => (
  <div class="cm-catbars">
    {CATS.filter((c) => by[c.id]).sort((a, b) => (by[b.id] ?? 0) - (by[a.id] ?? 0)).map((c) => (
      <div class="r" style={{ "--c": catVar(c.id) } as any}>
        <span class="l"><span aria-hidden="true">{c.emoji}</span> {c.label}</span>
        <span class="bar"><i style={{ width: `${(100 * (by[c.id] ?? 0)) / Math.max(1, total)}%` }} /></span>
        <b class="num">{by[c.id]}</b>
      </div>
    ))}
  </div>
);

export function MembersPage() {
  const { data, error, loading } = useApi<{ people: Record<string, PersonInfo>; members: Attendance[] }>("/api/community/members");
  if (data) setPeopleInfo(data.people);
  useEffect(() => { document.title = "Community · Members"; }, []);
  useTheme();
  const ms = data?.members ?? [];
  return (
    <main class="cm">
      <PageHeader title="Members" sub={data ? plural(ms.length, "member") : loading ? "Loading…" : ""}
        about="Everyone who has trained with the club: how often, how recently, and how they do against their own form."
        tips={["A member is anyone with a watch file in a joint session", "Streak = consecutive weeks with a club session", "Click a card for the member's profile"]} />
      {error && <div class="err">{error}</div>}
      {data && !ms.length && <div class="empty-state"><p>No members yet: they appear with the first joint session.</p></div>}
      <div class="cm-members">
        {ms.map((m) => {
          const c = personColour(m.person);
          return (
            <a class="cm-member" href={`/community/members/${encodeURIComponent(m.person)}`} style={{ "--p": c } as any}>
              <div class="top">
                <Av p={m.person} size="xl" />
                <div class="nm"><b>{personName(m.person)}</b><span class="muted small">since {m.first_seen ? shortDate(m.first_seen, "x") : "—"}</span></div>
                {m.streak_weeks > 0 && <span class="streak" title={`${m.streak_weeks} weeks in a row`}>🔥 {m.streak_weeks} wk</span>}
              </div>
              <div class="cm-stats sm">
                <Stat v={m.sessions} label="sessions" sub={`${Math.round(m.share * 100)} % of all`} />
                <Stat v={Math.round(m.km)} label="km" />
                <Stat v={m.hours.toFixed(1)} label="hours" />
                <Stat v={<FormChip v={m.avg_form_delta} showNone info={{ delta: m.avg_form_delta, text: `average over ${plural(m.n_form, "session")} with a score` }} />} label="avg form" />
              </div>
              <div class="cm-mix">{CATS.map((k) => m.by_category[k.id] ? <i style={{ flex: m.by_category[k.id], background: catVar(k.id) }} title={`${k.label}: ${m.by_category[k.id]}`} /> : null)}</div>
              {(m.company?.text || m.badges) && (
                <div class="cm-mcard-x">
                  {m.company?.text && <span class={"cmx-company " + (m.company.verdict ?? "")} title={`together ${delta(m.company.joint_avg)} (${m.company.n_joint_cmp ?? m.company.n_joint}) vs alone ${delta(m.company.solo_avg)} (${m.company.n_solo_cmp ?? m.company.n_solo})${m.company.thin ? " · thin data" : ""}`}>
                    {m.company.verdict === "company" ? <><b>👯 {delta(m.company.diff)}</b> with company</> : m.company.verdict === "alone" ? <><b>🧘 {Math.abs(m.company.diff ?? 0).toFixed(1)}</b> better alone</> : <><b>⚖️ same</b> together or alone</>}</span>}
                  {m.badges && <span class="cmx-badges" title={m.badges.latest.map((b) => `${b.emoji} ${b.name} (${b.date})`).join("\n")}>
                    {m.badges.latest.slice(0, 4).map((b) => <span aria-hidden="true">{b.emoji}</span>)} <b>{m.badges.earned}</b>/{m.badges.total} badges</span>}
                  {m.badges?.next && <span class="cmx-next muted small">next: {m.badges.next.emoji} {m.badges.next.name} · {Math.round(m.badges.next.progress * 100)} %</span>}
                </div>
              )}
              <div class="foot muted small">
                <span>Last seen {ago(m.days_since)}</span>
                {m.favourite_place && <span>· ♥ {m.favourite_place}</span>}
                <span>· best streak {m.best_streak_weeks} wk</span>
              </div>
            </a>
          );
        })}
      </div>
      {ms.length > 1 && (
        <section class="cm-panel cm-cmp">
          <div class="head"><h2>Side by side</h2><span class="sub">every member on the same scale</span></div>
          <div class="cm-cmp-grid">
            {([["sessions", "Sessions", (a: Attendance) => a.sessions, (v: number) => String(v)],
               ["km", "Kilometres", (a: Attendance) => a.km, (v: number) => String(Math.round(v))],
               ["hours", "Hours", (a: Attendance) => a.hours, (v: number) => v.toFixed(1)],
               ["reps", "Reps", (a: Attendance) => a.reps, (v: number) => String(v)],
               ["streak", "Best streak (weeks)", (a: Attendance) => a.best_streak_weeks, (v: number) => String(v)],
               ["weeks", "Active weeks", (a: Attendance) => a.weeks_active, (v: number) => String(v)]] as const).map(([k, label, f, fmt]) => {
              const max = Math.max(1e-9, ...ms.map(f));
              return (
                <div class="metric" key={k}>
                  <span class="lbl">{label}</span>
                  {[...ms].sort((a, b) => f(b) - f(a)).map((a) => (
                    <a class="r" href={`/community/members/${encodeURIComponent(a.person)}`}>
                      <Av p={a.person} size="xs" />
                      <span class="bar"><i style={{ width: `${(100 * f(a)) / max}%`, background: personColour(a.person) }} /></span>
                      <b class="num">{fmt(f(a))}</b>
                    </a>
                  ))}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}

function SessionRow({ s }: { s: MemberSession }) {
  const n = s.numbers;
  return (
    <a class="cm-srow" href={`/community/${s.gid}`} style={{ "--c": catVar(s.category) } as any}>
      <span class="d">{shortDate(s.date, isoLocal())}<span class="muted t">{s.start_local}</span></span>
      <span class="c"><CatChip id={s.category} /></span>
      <span class="w"><b>{s.title ?? s.group_workout}</b><span class="muted">{s.place ?? ""}{s.workout && s.workout !== s.group_workout ? ` · own: ${s.workout}` : ""}</span></span>
      <span class="with">{s.with.map((p) => <Av key={p} p={p} size="sm" />)}</span>
      <span class="rk">{s.rank && s.of_people ? (s.rank === 1 ? "🥇" : `${s.rank}/${s.of_people}`) : ""}</span>
      <span class="f"><FormChip v={s.form?.delta ?? null} info={s.form} />{s.form && <span class="muted small"> {s.form.score.toFixed(1)}</span>}</span>
      <span class="num">{n?.km ? `${n.km.toFixed(1)} km` : "—"}</span>
      <span class="num muted">{dur(n?.moving_s)}</span>
      <span class="num muted">{n?.avg_hr ? `${Math.round(n.avg_hr)} bpm` : ""}</span>
      <span class="ex">{s.n_photos > 0 && <span>📷</span>}{s.kudos > 0 && <span class="kudos">♥{s.kudos}</span>}</span>
      <span class="go" aria-hidden="true">›</span>
    </a>
  );
}

export function MemberPage({ person }: { person: string }) {
  const { data: m, error, loading } = useApi<Member>(`/api/community/members/${encodeURIComponent(person)}`);
  const club = useApi<{ people: Record<string, PersonInfo> }>("/api/community/members").data;
  if (club) setPeopleInfo(club.people);
  if (m) setPeopleInfo({ [m.person]: m.display });
  useEffect(() => { document.title = `Community · ${personName(person)}`; }, [person]);
  useTheme();
  if (error) return <main class="cm"><a class="cm-back" href="/community/members">‹ Members</a><h1>Member not found</h1><p class="note">{/^(404|no member)/.test(error) ? <>There is no club member “{person}”. <a href="/community/members">See all members →</a></> : error}</p></main>;
  if (!m) return <main class="cm"><a class="cm-back" href="/community/members">‹ Members</a><p class="muted">{loading ? "Loading…" : ""}</p></main>;
  const t = m.totals;
  const best = m.best_sessions.filter((s) => s.form && s.form.delta > 0);
  const shapeOk = m.shape && Object.values(m.shape.disciplines).some((d) => d.weekly?.length);
  const fs = m.form_series;
  const avgUp = fs.filter((s) => s.delta > 0).length;
  return (
    <main class="cm">
      <a class="cm-back" href="/community/members">‹ Members</a>
      <div class="cm-profile" style={{ "--p": personColour(m.person) } as any}>
        <Av p={m.person} size="xl" />
        <div class="nm">
          <h1>{personName(m.person)}</h1>
          <span class="muted">{t.first ? `Member since ${shortDate(t.first, "x")}` : ""}{t.last ? ` · last session ${shortDate(t.last, isoLocal())}` : ""}</span>
        </div>
        {t.streak_weeks > 0 && <span class="streak big" title="consecutive weeks with a club session">🔥 {t.streak_weeks}-week streak</span>}
      </div>
      <div class="cm-stats wide">
        <Stat v={t.sessions} label="sessions" />
        <Stat v={Math.round(t.km)} label="km" />
        <Stat v={t.hours.toFixed(1)} label="hours" />
        <Stat v={t.reps} label="reps" />
        <Stat v={t.climb_m.toLocaleString("en-GB")} label="m climbed" />
        <Stat v={<FormChip v={t.avg_form_delta} showNone />} label="avg form" sub={fs.length ? `${avgUp} of ${fs.length} above form` : ""} />
        <Stat v={`${t.best_streak_weeks} wk`} label="best streak" sub={`${t.weeks_active} active weeks`} />
      </div>

      <div class="cm-grid">
        <section class="cm-panel span2">
          <div class="head"><h2>Badges</h2><span class="sub">earned in club sessions · locked ones show how close you are</span></div>
          <BadgeGrid badges={m.badges.badges} earned={m.badges.earned} total={m.badges.total} />
        </section>

        <section class="cm-panel">
          <div class="head"><h2>Faster together than alone?</h2><span class="sub">club vs solo sessions of the same kind, ±{m.company.window_days} days</span></div>
          <CompanyCard p={m.person} c={m.company} full />
          <div class="cc-note">Each dot = one session's form delta (score − shape the day before); the bar = the average over the sessions that have a partner to compare with (same kind, within 60 days). Together = club sessions, alone = {personName(m.person)}'s own sessions of the same discipline near a club one. Click a dot to open it.</div>
        </section>

        <section class="cm-panel">
          <div class="head"><h2>Form in club sessions</h2><span class="sub">score − shape the day before</span></div>
          {fs.length ? <ClubFormChart series={fs} /> : <div class="empty">No scored sessions yet (easy runs and hikes have no score).</div>}
        </section>
        <section class="cm-panel">
          <div class="head"><h2>Shape trend</h2><span class="sub">{m.shape ? Object.entries(m.shape.disciplines).filter(([, d]) => d.current).map(([k, d]) => `${DISC_LABEL[k] ?? k} ${d.current!.mean.toFixed(1)}`).join(" · ") : ""}</span><a class="sub" href="/fitness">Fitness ›</a></div>
          {shapeOk ? <ClubShapeChart shape={m.shape!} sessions={fs} /> : <div class="empty">No shape model for {personName(m.person)} (scripts/fitness.py).</div>}
        </section>

        <section class="cm-panel">
          <div class="head"><h2>Sessions by category</h2></div>
          <CatBars by={m.by_category} total={t.sessions} />
        </section>

        <section class="cm-panel">
          <div class="head"><h2>Training partners</h2></div>
          {m.partners.length ? m.partners.map((p) => (
            <div class="cm-partner">
              <div class="row"><PersonLink p={p.person} size="lg" /><span class="muted">{plural(p.sessions, "session")} together · last {shortDate(p.last, isoLocal())}</span></div>
            </div>
          )) : <div class="empty">No partners yet.</div>}
        </section>

        {best.length > 0 && (
          <section class="cm-panel span2">
            <div class="head"><h2>Best sessions</h2><span class="sub">furthest above form</span></div>
            <div class="cm-best">
              {best.map((s, i) => (
                <a class="cm-bs" href={`/community/${s.gid}`} style={{ "--c": catVar(s.category) } as any}>
                  <span class="medal">{["🥇", "🥈", "🥉"][i] ?? "⭐"}</span>
                  <b class={"v " + (s.form!.delta >= 0 ? "up" : "down")}>{delta(s.form!.delta)}</b>
                  <span class="w">{s.title ?? s.group_workout}</span>
                  <span class="muted small">{shortDate(s.date, isoLocal())} · {s.place}</span>
                  <span class="muted small">score {s.form!.score.toFixed(1)} vs {s.form!.shape.toFixed(1)}{s.with.length ? <> · with {s.with.map((p) => <Av p={p} size="xs" />)}</> : null}</span>
                </a>
              ))}
            </div>
          </section>
        )}

        <section class="cm-panel span2">
          <div class="head"><h2>All sessions</h2><span class="sub">{plural(m.sessions.length, "session")} · {kms(t.km)} · {hrs(t.hours)}</span></div>
          <div class="cm-srows">{m.sessions.map((s) => <SessionRow key={s.gid} s={s} />)}</div>
        </section>
      </div>
    </main>
  );
}
