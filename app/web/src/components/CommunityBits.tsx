// Small shared pieces of the Community pages: the form delta as plain text, a person link, the members table
// (Club attendance and the Members page) and a debounced search box.
import { useEffect, useState } from "preact/hooks";
import { setQuery } from "../lib/router";
import { personName } from "../lib/people";
import { ago, delta, plural, type Attendance, type FormInfo } from "../lib/community";

type N = number | null;

/** a form delta as text ("+2.4" / "−1.6"); coloured only when |delta| ≥ 2; details in the tooltip */
export function FormDelta({ v, info, p }: { v: N | undefined; info?: FormInfo | null; p?: string }) {
  if (v === null || v === undefined) return <span class="muted" title="no form score (easy run, hike, no watch data)">—</span>;
  if (info?.confidence === "none") {   // rejected by the fitness model itself: no number
    return <span class="muted" title={(p ? personName(p) + ": " : "") + `score unreliable (${delta(v)} vs form, rejected by the fitness model)`}>?</span>;
  }
  const tip = [
    p ? personName(p) : null,
    info?.text ?? `${delta(v)} vs form`,
    info?.score != null && info?.shape != null ? `score ${info.score.toFixed(1)} vs shape ${info.shape.toFixed(1)} the day before` : null,
    info?.confidence ? `${info.confidence} confidence` : null,
  ].filter(Boolean).join(" · ");
  const cls = v >= 2 ? "cm-up" : v <= -2 ? "cm-down" : "";
  return <span class={cls} title={tip}>{delta(v)}</span>;
}

export const PersonLink = ({ p }: { p: string }) => <a href={`/community/members/${encodeURIComponent(p)}`}>{personName(p)}</a>;

/** one row per member: sessions, km, hours, reps, average form, last seen, best streak */
export function MembersTable({ rows }: { rows: Attendance[] }) {
  return (
    <div class="tablewrap cm-tw">
      <table class="cm-tbl">
        <thead><tr>
          <th>Member</th><th class="num">Sessions</th><th class="num">km</th><th class="num">Hours</th><th class="num">Reps</th>
          <th class="num" title="Mean form delta (session score minus shape the day before) over the sessions with a score">Avg form</th>
          <th>Last seen</th><th class="num" title="Most consecutive weeks with a club session">Best streak</th>
        </tr></thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.person}>
              <td><PersonLink p={a.person} /></td>
              <td class="num">{a.sessions}</td>
              <td class="num">{Math.round(a.km)}</td>
              <td class="num">{a.hours.toFixed(1)}</td>
              <td class="num">{a.reps}</td>
              <td class="num"><FormDelta v={a.avg_form_delta} info={{ delta: a.avg_form_delta, text: `average over ${plural(a.n_form, "session")} with a score` }} /></td>
              <td>{a.last_gid ? <a href={`/community/${a.last_gid}`}>{ago(a.days_since)}</a> : "—"}</td>
              <td class="num">{a.best_streak_weeks} wk</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SearchBox({ value, placeholder = "Search…" }: { value: string; placeholder?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const t = setTimeout(() => setQuery({ q: v.trim() }), 350);
    return () => clearTimeout(t);
  }, [v]);
  return <input type="search" value={v} placeholder={placeholder} class="cm-q" aria-label="Search"
    onInput={(e) => setV((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === "Enter" && setQuery({ q: v.trim() })} />;
}
