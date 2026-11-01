// Together: every workout done with someone else (data/together.json via /api/together). Head-to-head strip per
// pair on top, filters in the URL (people, sport, label + near-misses, place, period, words), one row per joint
// session (newest first) linking to /together/<gid>. Groups are global; the person prefix only means "viewing as".
//   /together?people=adrian,patrik&sport=run&label=same_workout,same_race&near=1&place=Bislet&period=2025&q=400
import { useEffect, useMemo, useState } from "preact/hooks";
import { useApi, qs } from "../lib/api";
import { people as allPeople, setQuery, useLocation } from "../lib/router";
import { SPORT_LABEL, addDays, isoLocal, shortDate } from "../lib/format";
import { personColour, personInfo, personName, setPeopleInfo } from "../lib/people";
import { useTheme } from "../lib/theme";
import { KIND_LABEL, LABELS, labelShort, placeText, type H2H, type TGroup, type TList } from "../lib/together";
import { PageHeader } from "../components/PageHeader";
import "./together.css";

/** person avatar: initials on the person's colour */
export function Avatar({ p, role, size, dim }: { p: string; role?: string; size?: "sm" | "lg"; dim?: boolean }) {
  useTheme();
  const i = personInfo(p);
  const roleText = role === "partial" ? " · partial (part of the workout)" : role === "other" ? " · other workout" : "";
  return (
    <span class={"tg-av" + (size ? " " + size : "") + (role === "other" || dim ? " dim" : "")} style={{ background: personColour(p) }} title={personName(p) + roleText}>
      {i.initials}{role === "partial" && <i class="role" aria-hidden="true">◐</i>}
    </span>
  );
}

function periods(years: string[]) {
  const today = isoLocal();
  return [
    { id: "90d", label: "Last 90 days", from: addDays(today, -89), to: "" },
    { id: "12m", label: "Last 12 months", from: addDays(today, -364), to: "" },
    ...years.map((y) => ({ id: y, label: y, from: `${y}-01-01`, to: `${y}-12-31` })),
  ];
}

function SearchBox({ value }: { value: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const t = setTimeout(() => setQuery({ q: v.trim() }), 400);
    return () => clearTimeout(t);
  }, [v]);
  return <input type="search" value={v} placeholder="Search place, workout, date…" class="tg-q" aria-label="Search"
    onInput={(e) => setV((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === "Enter" && setQuery({ q: v.trim() })} />;
}

/** one pair's record, oriented viewer first */
function PairCard({ h, viewer }: { h: H2H; viewer: string | null }) {
  const flip = viewer === h.b;
  const [a, b] = flip ? [h.b, h.a] : [h.a, h.b];
  const gap = h.mean_gap_s === null ? null : flip ? -h.mean_gap_s : h.mean_gap_s;   // positive: a faster
  const wa = h.rep_wins[a] ?? 0, wb = h.rep_wins[b] ?? 0;
  const sa = h.session_wins[a] ?? 0, sb = h.session_wins[b] ?? 0;
  return (
    <div class="tg-pair">
      <div class="tg-pair-score">
        <span class="who"><Avatar p={a} />{personName(a)}</span>
        <b class="score" title="Reps won on shared reps">{h.shared_reps ? <>{wa}<span class="dash">–</span>{wb}</> : "—"}</b>
        <span class="who">{personName(b)}<Avatar p={b} /></span>
      </div>
      <div class="tg-pair-sub">
        {h.shared_reps ? <span>reps won of {h.shared_reps} shared{h.rep_ties ? ` · ${h.rep_ties} tied` : ""}</span> : <span>no shared reps yet</span>}
        {h.interval_sessions > 0 && <span>sessions {sa}–{sb}</span>}
        {gap !== null && h.shared_reps > 0 && <span title="mean difference per shared rep">{personName(gap >= 0 ? a : b)} {Math.abs(gap).toFixed(1)} s faster per rep</span>}
        <span>{h.sessions} session{h.sessions === 1 ? "" : "s"}</span>
      </div>
    </div>
  );
}

function Headline({ g }: { g: TGroup }) {
  const h = g.headline;
  if (!h) return null;
  if (h.kind === "unverified" || g.no_track) return <span class="muted">{h.text}</span>;
  const parts = h.text.split(" · ");
  const lead = h.kind === "intervals" || h.kind === "race" ? parts.shift() : null;
  return (
    <span class="tg-hl">
      {lead && <b style={{ color: h.winner ? personColour(h.winner) : undefined }}>{h.kind === "race" ? "🏁 " : ""}{lead}</b>}
      {parts.length > 0 && <span class="muted">{lead ? " · " : ""}{parts.join(" · ")}</span>}
    </span>
  );
}

function Row({ g, order }: { g: TGroup; order: string[] }) {
  const parts = [...g.participants].sort((a, b) => order.indexOf(a.person) - order.indexOf(b.person));
  const comp = g.kind === "intervals" && parts.some((p) => p.of)
    ? parts.map((p) => `${personInfo(p.person).initials} ${p.reps_done ?? 0}/${p.of ?? "?"}`).join(" · ") : null;
  const span0 = parts.find((p) => p.together)?.together?.text;
  const span = span0 && span0 !== g.headline?.text ? span0 : null;   // not twice
  const other = parts.filter((p) => p.role === "other").map((p) => personName(p.person));
  return (
    <a class={"tg-row" + (g.no_track ? " unverified" : "") + (g.hidden ? " near" : "")} href={`/together/${g.gid}`}>
      <span class="d">{shortDate(g.date, isoLocal())}<span class="muted t">{g.start_local}</span></span>
      <span class="pl" title={placeText(g.place)}>{g.place?.name ?? "—"}</span>
      <span class="wk">
        <span class="kind">{KIND_LABEL[g.kind] ?? g.kind}</span>
        <span class="w" title={g.workout}>{g.workout}</span>
        {(comp || span || other.length > 0) && <span class="sub muted">{[comp, span, other.length ? `${other.join(", ")}: other workout` : ""].filter(Boolean).join(" · ")}</span>}
      </span>
      <span class="ppl">{parts.map((p) => <Avatar key={p.person + p.activity_id} p={p.person} role={p.role} />)}</span>
      <span class="lb"><span class={"tg-badge " + g.label} title={LABELS.find((l) => l.id === g.label)?.hint}>{labelShort(g.label)}</span>{g.no_track && g.label !== "unverified" && <span class="tg-badge notrack">no track</span>}</span>
      <span class="hl"><Headline g={g} /></span>
      <span class="go" aria-hidden="true">›</span>
    </a>
  );
}

export function TogetherPage() {
  const { query } = useLocation();
  const people = (query.get("people") ?? "").split(",").filter(Boolean);
  const sport = query.get("sport") ?? "";
  const labels = (query.get("label") ?? "").split(",").filter(Boolean);
  const near = query.get("near") === "1";
  const place = query.get("place") ?? "";
  const period = query.get("period") ?? "";
  const q = query.get("q") ?? "";

  const all = useApi<TList>("/api/together?near=1");
  const years = useMemo(() => [...new Set((all.data?.groups ?? []).map((g) => g.date.slice(0, 4)))].sort().reverse(), [all.data]);
  const per = periods(years).find((p) => p.id === period);
  const url = "/api/together" + qs({ person: people.join(","), sport, label: labels.join(","), near: near ? 1 : null, place,
    from: per?.from, to: per?.to, q });
  const { data, error, loading } = useApi<TList>(url);
  if (all.data) setPeopleInfo(all.data.people);
  useEffect(() => { document.title = "Together"; }, []);

  const opts = useMemo(() => {
    const gs = all.data?.groups ?? [];
    const count = <T extends string>(f: (g: TGroup) => T[]) => {
      const m = new Map<T, number>();
      for (const g of gs) for (const k of f(g)) m.set(k, (m.get(k) ?? 0) + 1);
      return m;
    };
    const shown = gs.filter((g) => !g.hidden);
    const places = new Map<string, { name: string; n: number }>();
    for (const g of shown) if (g.place) { const o = places.get(g.place.id) ?? { name: g.place.name, n: 0 }; o.n++; places.set(g.place.id, o); }
    return {
      people: [...count((g) => g.people).keys()],
      sports: count((g) => (g.hidden ? [] : [g.sport])),
      labels: count((g) => [g.label]),
      places: [...places.entries()].sort((a, b) => b[1].n - a[1].n),
      nNear: gs.filter((g) => g.hidden).length,
    };
  }, [all.data]);

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]).join(",");
  const viewer = data?.viewer ?? all.data?.viewer ?? null;
  const anyFilter = people.length || sport || labels.length || near || place || period || q;
  const h2h = data?.h2h ?? [];
  const groups = data?.groups ?? [];
  // participants in one fixed order everywhere (the viewer first), so the initials line up from row to row
  const order = useMemo(() => [...(viewer ? [viewer] : []), ...allPeople(), ...opts.people], [viewer, opts]);

  return (
    <main class="tg">
      <PageHeader title="Together" sub={data ? `${data.n} session${data.n === 1 ? "" : "s"}${anyFilter ? " (filtered)" : ""}` : loading ? "Loading…" : ""}
        about="Workouts done with someone else: the same reps, side by side, or the same race. Who won each rep, gaps and a replay."
        tips={[
          "Same workout: interval sessions with the same reps started together, ranked rep by rep",
          "Side by side: a steady run or hike together (within ~50 m most of the time); Race: the same start, run apart",
          "Unverified: one of you has no watch file, so only the start place and time match",
          "Near-misses: the same place and time but different workouts (hidden unless you show them)",
          "The number next to Together in the sidebar counts your sessions in the last 30 days",
        ]} />

      {h2h.length > 0 && (
        <div class="tg-h2h">
          {h2h.slice(0, 3).map((h) => <PairCard key={h.a + h.b} h={h} viewer={viewer} />)}
          {h2h.length > 3 && <span class="muted">+{h2h.length - 3} more pairs</span>}
        </div>
      )}

      <div class="tg-filters">
        {opts.people.length > 2 && (
          <div class="chips" role="group" aria-label="People">
            {opts.people.map((p) => (
              <button key={p} class={"chip pchip" + (people.includes(p) ? " on" : "")} onClick={() => setQuery({ people: toggle(people, p) })}
                aria-pressed={people.includes(p)} title={`Sessions with ${personName(p)}`}><Avatar p={p} size="sm" />{personName(p)}</button>
            ))}
          </div>
        )}
        <div class="chips" role="group" aria-label="Sport">
          <button class={"chip" + (!sport ? " on" : "")} onClick={() => setQuery({ sport: null })}>All</button>
          {[...opts.sports.entries()].map(([s, n]) => (
            <button key={s} class={"chip" + (sport === s ? " on" : "")} onClick={() => setQuery({ sport: sport === s ? null : s })} title={`${n} sessions`}>
              {SPORT_LABEL[s] ?? s} <span class="n">{n}</span></button>
          ))}
        </div>
        <span class="sep" />
        <div class="chips" role="group" aria-label="Label">
          {LABELS.filter((l) => opts.labels.has(l.id)).map((l) => (
            <button key={l.id} class={"chip" + (labels.includes(l.id) ? " on" : "")} title={l.hint} aria-pressed={labels.includes(l.id)}
              onClick={() => setQuery({ label: toggle(labels, l.id) })}>{l.label} <span class="n">{opts.labels.get(l.id)}</span></button>
          ))}
          {opts.nNear > 0 && (
            <button class={"chip" + (near ? " on" : "")} aria-pressed={near} onClick={() => setQuery({ near: near ? null : 1 })}
              title="Same place and time but different workouts (e.g. only a shared warm-up): hidden by default">Show near-misses <span class="n">{opts.nNear}</span></button>
          )}
        </div>
        <span class="sep" />
        <select class="tg-place" value={place} onChange={(e) => setQuery({ place: (e.target as HTMLSelectElement).value })} aria-label="Place">
          <option value="">Any place</option>
          {place && !opts.places.some(([id]) => id === place) && <option value={place}>{place}</option>}
          {opts.places.map(([id, o]) => <option key={id} value={id}>{o.name} ({o.n})</option>)}
        </select>
        <select value={period} onChange={(e) => setQuery({ period: (e.target as HTMLSelectElement).value })} aria-label="Period">
          <option value="">Any time</option>
          {periods(years).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <SearchBox value={q} />
        {anyFilter ? <button class="link" onClick={() => setQuery({ people: null, sport: null, label: null, near: null, place: null, period: null, q: null })}>Clear</button> : null}
      </div>

      {error && <div class="err">{error}</div>}
      {data && !groups.length && (
        <div class="empty">{all.data?.groups.length ? "No sessions match these filters." : "No sessions with 2+ people yet. Detection runs when everyone's data is refreshed (scripts/together.py build)."}</div>
      )}
      {groups.length > 0 && (
        <div class="tg-list" role="list">
          {groups.map((g) => <Row key={g.gid} g={g} order={order} />)}
        </div>
      )}
    </main>
  );
}
