// Community · Sessions: the club's joint workouts as one dense table with a divider row per month
// (/api/community/sessions). Filters in the URL: type, person, period, words, near-misses.
//   /community?cat=intervals&people=adrian&period=2026|12m|90d|2026-10&q=bislett&near=1
// km and time are per person (the session's collective total ÷ members). Sessions without everyone's watch file are greyed.
import { Fragment } from "preact";
import { useEffect, useMemo } from "preact/hooks";
import { qs, useApi } from "../lib/api";
import { people as allPeople, navigate, setQuery, useLocation } from "../lib/router";
import { dur, isoLocal, shortDate } from "../lib/format";
import { personInfo, personName, setPeopleInfo } from "../lib/people";
import { placeText } from "../lib/together";
import { byMonth, catInfo, CATS, delta, monthLabel, periods, plural, resolvePeriod, type CGroup, type CList } from "../lib/community";
import { PageHeader } from "../components/PageHeader";
import { SearchBox } from "../components/CommunityBits";
import "./community.css";

/** members (with a watch file) first, then participants without one, each in the fixed person order */
function members(g: CGroup, order: string[]): string[] {
  const ps = [...new Set(g.participants.filter((p) => p.role !== "other").map((p) => p.person))].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const timed = new Set((g.timed ?? []).map((k) => k.split("#")[0]));
  return timed.size ? [...ps.filter((p) => timed.has(p)), ...ps.filter((p) => !timed.has(p))] : ps;
}

/** per person: the collective total ÷ the members with numbers */
const perPerson = (g: CGroup) => {
  const n = Math.max(1, g.collective?.n_members || g.n_timed || 1);
  return { km: (g.collective?.km ?? 0) / n, s: (g.collective?.moving_s ?? 0) / n };
};

/** "A +2.5 · P +0.8": initial and form delta per member, coloured only when |delta| ≥ 2 */
function FormText({ g, ms }: { g: CGroup; ms: string[] }) {
  const ks = (g.timed?.length ? g.timed : ms).filter((k) => g.form?.[k] !== null && g.form?.[k] !== undefined);
  if (!ks.length) return null;
  return (
    <>{ks.map((k, i) => {
      const v = g.form[k]!, info = g.form_info?.[k];
      const unrel = info?.confidence === "none";
      const cls = unrel ? "muted" : v >= 2 ? "cm-up" : v <= -2 ? "cm-down" : "";
      const tip = `${personName(k)}: ${unrel ? "score unreliable" : info?.text ?? `${delta(v)} vs form`}${info?.confidence && !unrel ? ` · ${info.confidence} confidence` : ""}`;
      return <Fragment key={k}>{i > 0 && " · "}<span class={cls} title={tip}>{personInfo(k.split("#")[0]).initials} {unrel ? "?" : delta(v)}</span></Fragment>;
    })}</>
  );
}

function Row({ g, order }: { g: CGroup; order: string[] }) {
  const ms = members(g, order);
  const pp = perPerson(g);
  const href = `/community/${g.gid}`;
  const go = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest("a")) navigate(href); };
  const missing = g.no_track ? "summary only: not everyone has a watch file" : undefined;
  return (
    <tr class={"cm-srow" + (g.no_track ? " grey" : "") + (g.hidden || g.user_hidden ? " grey" : "")} onClick={go} title={missing}>
      <td class="nowrap">{shortDate(g.date, isoLocal())}</td>
      <td><a href={href}>{g.display_title}</a></td>
      <td>{catInfo(g.category).label}</td>
      <td title={placeText(g.place)}>{g.place?.name ?? ""}</td>
      <td>{ms.map(personName).join(", ")}{g.guests.length > 0 && <span class="muted"> + {g.guests.join(", ")}</span>}</td>
      <td class="num">{pp.km ? pp.km.toFixed(1) : "—"}</td>
      <td class="num">{pp.s ? dur(pp.s) : "—"}</td>
      <td class="nowrap"><FormText g={g} ms={ms} /></td>
    </tr>
  );
}

export function CommunityPage() {
  const { query } = useLocation();
  const cat = (query.get("cat") ?? "").split(",").filter(Boolean)[0] ?? "";
  const person = (query.get("people") ?? "").split(",").filter(Boolean)[0] ?? "";
  const period = query.get("period") ?? "";
  const q = query.get("q") ?? "";
  const near = query.get("near") === "1";

  const all = useApi<CList>("/api/community/sessions?near=1");
  const per = resolvePeriod(period);
  const url = "/api/community/sessions" + qs({ category: cat, person, from: per?.from, to: per?.to, q, near: near ? 1 : null });
  const { data, error, loading } = useApi<CList>(url);
  if (all.data) setPeopleInfo(all.data.people);
  if (data) setPeopleInfo(data.people);
  useEffect(() => { document.title = "Community · Sessions"; }, []);

  const years = useMemo(() => [...new Set((all.data?.groups ?? []).map((g) => g.date.slice(0, 4)))].sort().reverse(), [all.data]);
  const everyone = useMemo(() => {
    const m = new Map<string, number>();
    for (const g of all.data?.groups ?? []) if (!g.hidden) for (const p of g.people) m.set(p, (m.get(p) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([p]) => p);
  }, [all.data]);
  const nNear = (all.data?.groups ?? []).filter((g) => g.hidden).length;
  const viewer = data?.viewer ?? all.data?.viewer ?? null;
  const order = useMemo(() => [...(viewer ? [viewer] : []), ...allPeople(), ...everyone], [viewer, everyone]);
  const groups = data?.groups ?? [];
  const months = useMemo(() => byMonth(groups), [groups]);
  const anyFilter = cat || person || period || q || near;
  const clear = () => setQuery({ cat: null, people: null, period: null, q: null, near: null });
  const sel = (key: string) => (e: Event) => setQuery({ [key]: (e.target as HTMLSelectElement).value || null });

  return (
    <main class="cm">
      <PageHeader title="Sessions" sub={data ? `${plural(data.n, "session")}${anyFilter ? " (filtered)" : ""}` : loading ? "Loading…" : ""}
        about="The club's joint workouts."
        tips={[
          "km and time are per person (the session's total ÷ members)",
          "Form = the session's fitness score minus the member's shape the day before (VDOT points); coloured when 2 or more",
          "Greyed rows: not everyone has a watch file (summary only), or a near-miss",
          "Types are automatic; change one on the session page (Edit)",
        ]} />

      <div class="cm-filters">
        <select value={cat} onChange={sel("cat")} aria-label="Type">
          <option value="">All types</option>
          {CATS.map((c) => <option key={c.id} value={c.id}>{(data?.categories ?? []).find((x) => x.id === c.id)?.label ?? c.label}</option>)}
        </select>
        {everyone.length > 1 && (
          <select value={person} onChange={sel("people")} aria-label="Person">
            <option value="">Everyone</option>
            {everyone.map((p) => <option key={p} value={p}>{personName(p)}</option>)}
          </select>
        )}
        <select value={period} onChange={sel("period")} aria-label="Period">
          <option value="">All time</option>
          {per && !periods(years).some((p) => p.id === period) && <option value={period}>{per.label}</option>}
          {periods(years).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <SearchBox value={q} placeholder="Search title, place, workout, note…" />
        {nNear > 0 && (
          <label title="Same place and time but different workouts (e.g. only a shared warm-up): hidden by default">
            <input type="checkbox" checked={near} onChange={() => setQuery({ near: near ? null : 1 })} /> Near-misses ({nNear})
          </label>
        )}
        {anyFilter ? <button class="link" onClick={clear}>Clear</button> : null}
      </div>

      {error && <div class="err">{error}</div>}
      {data?.store_error && <div class="err">community.json could not be read: {data.store_error}</div>}
      {data && !groups.length && (
        <p class="muted">{all.data?.groups.length ? "No sessions match these filters." : "No joint sessions yet. They appear once two members' watch files show the same workout (scripts/together.py build)."}</p>
      )}
      {groups.length > 0 && (
        <div class="tablewrap cm-tw">
          <table class="cm-tbl cm-sessions">
            <thead><tr>
              <th>Date</th><th>Workout</th><th>Type</th><th>Place</th><th>Who</th>
              <th class="num" title="Per person (average)">km</th><th class="num" title="Moving time per person (average)">Time</th>
              <th title="Each member's form delta: session score minus shape the day before">Form</th>
            </tr></thead>
            <tbody>
              {months.map(({ month, rows }) => {
                const km = rows.reduce((s, g) => s + perPerson(g).km, 0);
                return (
                  <Fragment key={month}>
                    <tr class="cm-month"><td colSpan={8}>{monthLabel(month)} · {plural(rows.length, "session")} · {Math.round(km)} km</td></tr>
                    {rows.map((g) => <Row key={g.gid} g={g} order={order} />)}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
