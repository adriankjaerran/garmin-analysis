// Community · Club: the club dashboard (/api/community/club?from=&to=), plain: one stats line (period | this year),
// the goal as text, "faster together than alone?" one line per member, the month chart, highlights, the members
// table, types, places and guests.
//   /community/club?period=2026|12m|90d
import { useEffect, useMemo } from "preact/hooks";
import { qs, useApi } from "../lib/api";
import { setQuery, useLocation } from "../lib/router";
import { isoLocal, shortDate } from "../lib/format";
import { setPeopleInfo } from "../lib/people";
import { HIGHLIGHT, hrs, kms, periods, plural, resolvePeriod, type Club } from "../lib/community";
import { PageHeader } from "../components/PageHeader";
import { MembersTable } from "../components/CommunityBits";
import { ClubMonthChart } from "../components/ClubCharts";
import { CompanyLines, GoalLine } from "../components/ClubMotivation";
import "./community.css";

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

export function ClubPage() {
  const { query } = useLocation();
  const period = query.get("period") ?? "";
  const per = resolvePeriod(period);
  const allTime = useApi<Club>("/api/community/club");
  const { data: c, error, loading } = useApi<Club>("/api/community/club" + qs({ from: per?.from, to: per?.to }));
  if (c) setPeopleInfo(c.people);
  useEffect(() => { document.title = "Community · Club"; }, []);

  const years = useMemo(() => {
    const t = allTime.data?.totals;
    if (!t?.first || !t.last) return [];
    const out: string[] = [];
    for (let y = Number(t.last.slice(0, 4)); y >= Number(t.first.slice(0, 4)); y--) out.push(String(y));
    return out;
  }, [allTime.data]);

  const t = c?.totals;
  // month chart: all time = the last 36 months when longer (a period shows all its months)
  const months = useMemo(() => {
    const m = c?.months ?? [];
    return !per && m.length > 36 ? m.slice(-36) : m;
  }, [c, per]);
  const catTotal = (c?.categories ?? []).reduce((s, x) => s + x.n, 0);
  const company = c ? c.attendance.filter((a) => c.company[a.person]).map((a) => ({ p: a.person, c: c.company[a.person] })) : [];

  return (
    <main class="cm">
      <PageHeader title="Club" sub={t ? `${plural(t.sessions, "session")} · ${plural(t.members, "member")}${per ? ` · ${per.label}` : ""}` : loading ? "Loading…" : ""}
        about="The running club at a glance."
        tips={[
          "km and hours are summed over all members",
          "Avg form = the mean of a member's form deltas (session score minus shape the day before)",
          "Best streak = most consecutive weeks with a club session",
          "Click a month in the chart to list its sessions",
        ]}>
        <PeriodSelect period={period} years={years} />
      </PageHeader>
      {error && <div class="err">{error}</div>}
      {c && t && <>
        <div class="cm-statline">
          <span><b>{per ? per.label : "All time"}</b> {plural(t.sessions, "session")} · {Math.round(t.km).toLocaleString("en-GB")} km · {hrs(t.hours)} · {t.reps} reps · {plural(t.places, "place")}
            {t.first && <span class="muted"> · {shortDate(t.first, isoLocal())} – {t.last ? shortDate(t.last, isoLocal()) : ""}</span>}</span>
          {!per && t.this_year && <span><b>{t.this_year.year}</b> {plural(t.this_year.sessions, "session")} · {Math.round(t.this_year.km)} km · {hrs(t.this_year.hours)}</span>}
        </div>

        <GoalLine initial={c.goal} />

        <section class="cm-sec">
          <h2>Members</h2>
          <MembersTable rows={c.attendance} />
        </section>

        {company.length > 0 && (
          <section class="cm-sec">
            <h2 title="Form delta in club sessions vs solo sessions of the same kind (±60 days)">Faster together than alone?</h2>
            <CompanyLines rows={company} />
          </section>
        )}

        <section class="cm-sec">
          <h2>Month by month{!per && c.months.length > 36 ? <span class="muted small"> · last 36 months</span> : null}</h2>
          <ClubMonthChart months={months} />
        </section>

        {c.highlights.length > 0 && (
          <section class="cm-sec">
            <h2>Highlights</h2>
            <ul class="cm-lines">
              {c.highlights.map((h) => (
                <li key={h.kind}><span class="muted">{HIGHLIGHT[h.kind] ?? h.kind}:</span> <a href={`/community/${h.gid}`}>{h.text}</a> <span class="muted">· {shortDate(h.date, isoLocal())}</span></li>
              ))}
            </ul>
          </section>
        )}

        <div class="cm-cols">
          <section class="cm-sec">
            <h2>Types</h2>
            <table class="cm-tbl">
              <thead><tr><th>Type</th><th class="num">Sessions</th><th class="num">Share</th><th class="num">km</th><th class="num">Hours</th></tr></thead>
              <tbody>
                {c.categories.filter((k) => k.n).sort((a, b) => b.n - a.n).map((k) => (
                  <tr key={k.id}><td><a href={`/community?cat=${k.id}`}>{k.label}</a></td><td class="num">{k.n}</td><td class="num">{catTotal ? Math.round((100 * k.n) / catTotal) : 0} %</td>
                    <td class="num">{kms(k.km)}</td><td class="num">{hrs(k.hours)}</td></tr>
                ))}
              </tbody>
            </table>
          </section>

          <section class="cm-sec">
            <h2>Places</h2>
            <table class="cm-tbl">
              <thead><tr><th>Place</th><th class="num">Sessions</th><th>Last</th></tr></thead>
              <tbody>
                {[...c.places].sort((a, b) => b.sessions - a.sessions || b.last.localeCompare(a.last)).slice(0, 10).map((p) => (
                  <tr key={p.id}><td><a href={`/community?q=${encodeURIComponent(p.name)}`}>{p.name}</a></td><td class="num">{p.sessions}</td><td>{shortDate(p.last, isoLocal())}</td></tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>

        {c.guests.length > 0 && (
          <p><span class="muted">Guests:</span> {c.guests.map((g) => `${g.name} (${g.visits})`).join(", ")}</p>
        )}
      </>}
    </main>
  );
}
