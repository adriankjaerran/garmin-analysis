// Community · Members: the members table (/api/community/members) and a member's profile
// (/api/community/members/<person>): totals, every session, form delta per session, company effect, partners.
//   /community/members · /community/members/<person>
import { useEffect } from "preact/hooks";
import { useApi } from "../lib/api";
import { dur, isoLocal, shortDate } from "../lib/format";
import { personName, setPeopleInfo, type PersonInfo } from "../lib/people";
import { catInfo, CATS, delta, hrs, kms, plural, type Attendance, type Member, type MemberSession } from "../lib/community";
import { PageHeader } from "../components/PageHeader";
import { FormDelta, MembersTable, PersonLink } from "../components/CommunityBits";
import { ClubFormChart } from "../components/ClubCharts";
import { CompanyLines } from "../components/ClubMotivation";
import "./community.css";

export function MembersPage() {
  const { data, error, loading } = useApi<{ people: Record<string, PersonInfo>; members: Attendance[] }>("/api/community/members");
  if (data) setPeopleInfo(data.people);
  useEffect(() => { document.title = "Community · Members"; }, []);
  const ms = data?.members ?? [];
  return (
    <main class="cm">
      <PageHeader title="Members" sub={data ? plural(ms.length, "member") : loading ? "Loading…" : ""}
        about="Everyone who has trained with the club."
        tips={["A member is anyone with a watch file in a joint session", "Avg form = mean of the session score minus shape the day before", "Best streak = most consecutive weeks with a club session"]} />
      {error && <div class="err">{error}</div>}
      {data && !ms.length && <p class="muted">No members yet: they appear with the first joint session.</p>}
      {ms.length > 0 && <MembersTable rows={ms} />}
    </main>
  );
}

function SessionRow({ s }: { s: MemberSession }) {
  const n = s.numbers;
  const own = s.workout && s.workout !== s.group_workout ? `own: ${s.workout}` : undefined;
  return (
    <tr>
      <td class="nowrap">{shortDate(s.date, isoLocal())}</td>
      <td><a href={`/community/${s.gid}`} title={own}>{s.title ?? s.group_workout}</a></td>
      <td>{catInfo(s.category).label}</td>
      <td>{s.place ?? ""}</td>
      <td>{s.with.map(personName).join(", ")}</td>
      <td class="num nowrap">{s.rank && s.of_people ? `${s.rank} of ${s.of_people}` : ""}</td>
      <td class="num"><FormDelta v={s.form?.delta ?? null} info={s.form} /></td>
      <td class="num">{n?.km ? n.km.toFixed(1) : "—"}</td>
      <td class="num">{dur(n?.moving_s)}</td>
      <td class="num">{n?.avg_hr ? Math.round(n.avg_hr) : ""}</td>
    </tr>
  );
}

export function MemberPage({ person }: { person: string }) {
  const { data: m, error, loading } = useApi<Member>(`/api/community/members/${encodeURIComponent(person)}`);
  const club = useApi<{ people: Record<string, PersonInfo> }>("/api/community/members").data;
  if (club) setPeopleInfo(club.people);
  if (m) setPeopleInfo({ [m.person]: m.display });
  useEffect(() => { document.title = `Community · ${personName(person)}`; }, [person]);
  if (error) return <main class="cm"><a href="/community/members">‹ Members</a><h1>Member not found</h1><p class="note">{/^(404|no member)/.test(error) ? <>There is no club member “{person}”. <a href="/community/members">See all members</a></> : error}</p></main>;
  if (!m) return <main class="cm"><a href="/community/members">‹ Members</a><p class="muted">{loading ? "Loading…" : ""}</p></main>;
  const t = m.totals;
  const fs = m.form_series;
  const avgUp = fs.filter((s) => s.delta > 0).length;
  const types = CATS.filter((c) => m.by_category[c.id]).sort((a, b) => (m.by_category[b.id] ?? 0) - (m.by_category[a.id] ?? 0));
  return (
    <main class="cm">
      <a href="/community/members">‹ Members</a>
      <h1 class="cm-h1">{personName(m.person)}</h1>
      <p class="cm-statline">
        <span>{plural(t.sessions, "session")} · {Math.round(t.km)} km · {hrs(t.hours)} · {t.reps} reps · {t.climb_m.toLocaleString("en-GB")} m climbed</span>
        <span title="Mean of session score minus shape the day before">avg form {delta(t.avg_form_delta)}{fs.length ? ` (${avgUp} of ${fs.length} above form)` : ""}</span>
        <span>best streak {t.best_streak_weeks} wk · {t.weeks_active} active weeks</span>
        <span class="muted">{t.first ? `since ${shortDate(t.first, "x")}` : ""}{t.last ? ` · last ${shortDate(t.last, isoLocal())}` : ""}</span>
      </p>

      <section class="cm-sec">
        <h2>All sessions <span class="muted small">· {plural(m.sessions.length, "session")} · {kms(t.km)} · {hrs(t.hours)}</span></h2>
        <div class="tablewrap cm-tw">
          <table class="cm-tbl">
            <thead><tr>
              <th>Date</th><th>Workout</th><th>Type</th><th>Place</th><th>With</th>
              <th class="num" title="Place in the session among the members who ran it: by average rep pace (intervals) or finish (races)">Rank</th>
              <th class="num" title="Session score minus shape the day before (VDOT points)">Form</th>
              <th class="num">km</th><th class="num">Time</th><th class="num" title="Average heart rate (bpm)">HR</th>
            </tr></thead>
            <tbody>{m.sessions.map((s) => <SessionRow key={s.gid} s={s} />)}</tbody>
          </table>
        </div>
      </section>

      <section class="cm-sec">
        <h2>Form in club sessions <span class="muted small">· score − shape the day before</span></h2>
        {fs.length ? <ClubFormChart series={fs} /> : <p class="muted">No scored sessions yet (easy runs and hikes have no score).</p>}
      </section>

      <section class="cm-sec">
        <h2 title={`Club vs solo sessions of the same kind, ±${m.company.window_days} days`}>Faster together than alone?</h2>
        <CompanyLines rows={[{ p: m.person, c: m.company }]} />
      </section>

      {(m.partners.length > 0 || types.length > 0) && (
        <section class="cm-sec">
          {m.partners.length > 0 && <p><span class="muted">Trained with:</span> {m.partners.map((p, i) => <>{i > 0 && ", "}<PersonLink p={p.person} /> ({p.sessions})</>)}</p>}
          {types.length > 0 && <p><span class="muted">Types:</span> {types.map((c) => `${c.label} ${m.by_category[c.id]}`).join(" · ")}</p>}
        </section>
      )}
    </main>
  );
}
