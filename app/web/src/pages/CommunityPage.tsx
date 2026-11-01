// Community · Sessions: the club's joint workouts as a card feed grouped by month (/api/community/sessions).
// Season totals + head-to-head on top, filters in the URL (category, people, period, words, near-misses, view).
//   /community?cat=intervals,race&people=adrian&period=2026|12m|90d|2026-10&q=bislett&near=1&view=list
// A card: cover photo (or a sketch of the members' tracks on the category colour), category, title, place / date,
// members (+ guests), headline, collective km / hours / reps, form delta per member, kudos / photos / records.
import { useEffect, useMemo, useState } from "preact/hooks";
import { qs, useApi } from "../lib/api";
import { people as allPeople, currentPerson, setQuery, useLocation } from "../lib/router";
import { dur, isoLocal, shortDate } from "../lib/format";
import { personColour, personName, setPeopleInfo } from "../lib/people";
import { placeText } from "../lib/together";
import { byMonth, catInfo, catVar, CATS, hrs, kms, monthLabel, periods, plural, resolvePeriod, type Category, type CGroup, type CList } from "../lib/community";
import { PageHeader } from "../components/PageHeader";
import { Av, CatChip, FormChip, PairCard, RouteSketch, SearchBox, Stat, usePeopleTracks } from "../components/CommunityBits";
import { kudosOverride, toggleKudos } from "../lib/communitySession";
import { GoalLink } from "../components/ClubMotivation";
import "./community.css";

function Headline({ g }: { g: CGroup }) {
  const h = g.headline;
  if (!h?.text) return null;
  if (h.kind === "unverified" || g.no_track) return <span class="muted">{h.text}</span>;
  const parts = h.text.split(" · ");
  const lead = h.kind === "intervals" || h.kind === "race" ? parts.shift() : null;
  return (
    <span class="cm-hl" title={h.text}>
      {lead && <b style={{ color: h.winner ? personColour(h.winner) : undefined }}>{h.kind === "race" ? "🏁 " : ""}{lead}</b>}
      {parts.length > 0 && <span class="muted">{lead ? " · " : ""}{parts.join(" · ")}</span>}
    </span>
  );
}

const membersText = (g: CGroup) => `${plural(g.n_timed, "member")}${g.n_guests ? ` · +${plural(g.n_guests, "guest")}` : ""}`;
/** members (with a watch file) first, then participants without one (dimmed), each in the fixed person order */
function members(g: CGroup, order: string[]): { p: string; timed: boolean }[] {
  const timed = new Set((g.timed ?? []).map((k) => k.split("#")[0]));
  const ps = [...new Set(g.participants.filter((p) => p.role !== "other").map((p) => p.person))].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return [...ps.filter((p) => !timed.size || timed.has(p)).map((p) => ({ p, timed: true })), ...ps.filter((p) => timed.size && !timed.has(p)).map((p) => ({ p, timed: false }))];
}
const Who = ({ ms, size }: { ms: { p: string; timed: boolean }[]; size?: "sm" }) => (
  <span class="cm-avs">{ms.map((m) => <Av key={m.p} p={m.p} size={size} dim={!m.timed} title={personName(m.p) + (m.timed ? "" : " · no watch file")} />)}</span>
);

function Cover({ g, tracks }: { g: CGroup; tracks: Map<string, string> }) {
  const lines = g.participants.filter((p) => tracks.get(p.activity_id)).map((p) => ({ person: p.person, poly: tracks.get(p.activity_id)!, dim: p.role === "other" }));
  const c = catInfo(g.category);
  return (
    <div class={"cm-cover" + (g.photo ? " photo" : "")} style={{ "--c": catVar(g.category) } as any}>
      {g.photo ? <img src={g.photo.thumb} alt="" loading="lazy" /> : <>
        <span class="wm" aria-hidden="true">{c.emoji}</span>
        {lines.length > 0 ? <RouteSketch lines={lines} /> : <span class="noline">{g.no_track ? "no track" : g.place?.name ?? ""}</span>}
      </>}
      <span class="tl"><CatChip id={g.category} solid /></span>
      <span class="tr">
        {g.n_records > 0 && <span class="cm-badge rec" title={g.records_short.join("\n")}>🏆 {g.n_records}</span>}
        {g.n_photos > 0 && <span class="cm-badge" title={`${g.n_photos} photo${g.n_photos === 1 ? "" : "s"}`}>📷 {g.n_photos}</span>}
      </span>
    </div>
  );
}

function Card({ g, tracks, order }: { g: CGroup; tracks: Map<string, string>; order: string[] }) {
  const ms = members(g, order);
  const col = g.collective;
  const forms = (g.timed?.length ? g.timed : ms.map((m) => m.p)).filter((k) => g.form?.[k] !== null && g.form?.[k] !== undefined);
  const sub = g.title && g.title !== g.workout ? g.workout : null;
  return (
    <a class={"cm-card" + (g.no_track ? " unverified" : "") + (g.hidden || g.user_hidden ? " near" : "")} href={`/community/${g.gid}`}>
      <Cover g={g} tracks={tracks} />
      <div class="body">
        <div class="ttl">
          <h3 title={g.display_title}>{g.display_title}</h3>
          <span class="when">{g.start_local}</span>
        </div>
        <div class="meta">
          <span>{shortDate(g.date, isoLocal())}</span>
          {g.place && <><span class="dotsep">·</span><span class="pl" title={placeText(g.place)}>{g.place.name}</span></>}
          {sub && <><span class="dotsep">·</span><span class="muted">{sub}</span></>}
        </div>
        <div class="who">
          <Who ms={ms} />
          <span class="n">{membersText(g)}</span>
          {g.guests.length > 0 && <span class="muted small" title={g.guests.join(", ")}>{g.guests.slice(0, 2).join(", ")}{g.guests.length > 2 ? "…" : ""}</span>}
        </div>
        <div class="hlrow"><Headline g={g} /></div>
        <div class="nums">
          <span title="Collective distance (all members)"><b>{kms(col.km)}</b></span>
          <span title="Collective moving time"><b>{hrs(col.hours)}</b></span>
          {col.reps > 0 && <span title="Reps run (all members)"><b>{col.reps}</b> reps</span>}
          {col.climb_m >= 150 && <span title="Metres climbed (all members)"><b>{col.climb_m.toLocaleString("en-GB")}</b> m ↑</span>}
          {col.person_min_shared > 0 && <span class="muted" title="Person-minutes on the move at the same time (sum over the members)">{Math.round(col.person_min_shared)} person-min</span>}
        </div>
        <div class="foot">
          <span class="forms">{forms.map((k) => <FormChip key={k} p={k} v={g.form[k]} info={g.form_info?.[k]} />)}</span>
          <KudosButton g={g} />
        </div>
      </div>
    </a>
  );
}

/** 👏 kudos as the URL's person, on a card / row (inside the link: the click doesn't open the session) */
function KudosButton({ g, compact }: { g: CGroup; compact?: boolean }) {
  const me = currentPerson();
  const [st, setSt] = useState(() => kudosOverride.get(g.gid) ?? { kudos: g.kudos, kudoed: g.kudoed ?? g.kudos.includes(me) });
  const [busy, setBusy] = useState(false);
  const [pop, setPop] = useState(false);
  useEffect(() => { setSt(kudosOverride.get(g.gid) ?? { kudos: g.kudos, kudoed: g.kudoed ?? g.kudos.includes(me) }); }, [g, me]);
  const n = st.kudos.length;
  const click = async (e: MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (busy || !me) return;
    const on = !st.kudoed;
    setBusy(true);
    setSt({ kudos: on ? [...st.kudos, me] : st.kudos.filter((p) => p !== me), kudoed: on });   // optimistic
    try { setSt(await toggleKudos(g.gid, me, on)); if (on) { setPop(true); setTimeout(() => setPop(false), 650); } }
    catch { setSt(st); } finally { setBusy(false); }
  };
  const who = n ? `Kudos from ${st.kudos.map(personName).join(", ")}` : "No kudos yet";
  return (
    <button class={"cm-kudos" + (st.kudoed ? " on" : "") + (pop ? " pop" : "") + (compact ? " compact" : "")} aria-pressed={st.kudoed}
      onClick={click} title={`${who} · ${st.kudoed ? `click to take back ${personName(me)}'s kudos` : `give kudos as ${personName(me)}`}`}
      aria-label={st.kudoed ? `Take back kudos (${n})` : `Give kudos (${n})`}>
      <span aria-hidden="true">👏</span>{n > 0 || !compact ? <b>{n || ""}</b> : null}
    </button>
  );
}

function Row({ g, order }: { g: CGroup; order: string[] }) {
  const ms = members(g, order);
  const forms = (g.timed?.length ? g.timed : ms.map((m) => m.p)).filter((k) => g.form?.[k] !== null && g.form?.[k] !== undefined);
  return (
    <a class={"cm-row" + (g.no_track ? " unverified" : "") + (g.hidden || g.user_hidden ? " near" : "")} href={`/community/${g.gid}`} style={{ "--c": catVar(g.category) } as any}>
      <span class="d">{shortDate(g.date, isoLocal())}<span class="muted t">{g.start_local}</span></span>
      <span class="c"><CatChip id={g.category} /></span>
      <span class="w"><b title={g.display_title}>{g.display_title}</b><span class="muted pl">{g.place?.name ?? ""}</span></span>
      <span class="ppl"><Who ms={ms} size="sm" />{g.n_guests > 0 && <span class="muted small">+{g.n_guests}</span>}</span>
      <span class="hl"><Headline g={g} /></span>
      <span class="forms">{forms.map((k) => <FormChip key={k} p={k} v={g.form[k]} info={g.form_info?.[k]} />)}</span>
      <span class="num">{kms(g.collective.km)}</span>
      <span class="num muted">{dur(g.collective.moving_s)}</span>
      <span class="ex">{g.n_records > 0 && <span title={g.records_short.join("\n")}>🏆</span>}{g.n_photos > 0 && <span>📷</span>}<KudosButton g={g} compact /></span>
      <span class="go" aria-hidden="true">›</span>
    </a>
  );
}

export function CommunityPage() {
  const { query } = useLocation();
  const cats = (query.get("cat") ?? "").split(",").filter(Boolean);
  const people = (query.get("people") ?? "").split(",").filter(Boolean);
  const period = query.get("period") ?? "";
  const q = query.get("q") ?? "";
  const near = query.get("near") === "1";
  const view = query.get("view") === "list" ? "list" : "cards";

  const all = useApi<CList>("/api/community/sessions?near=1");
  const per = resolvePeriod(period);
  const url = "/api/community/sessions" + qs({ category: cats.join(","), person: people.join(","), from: per?.from, to: per?.to, q, near: near ? 1 : null });
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
  const tracks = usePeopleTracks(view === "cards" ? everyone : []);

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]).join(",");
  const anyFilter = cats.length || people.length || period || q || near;
  const t = data?.totals;
  const catN = new Map((data?.categories ?? []).map((c) => [c.id, c.n]));
  const totalN = [...catN.values()].reduce((a, b) => a + b, 0);
  const periodLabel = per ? per.label : "All time";
  const goal = all.data?.goal ?? data?.goal ?? null;

  return (
    <main class="cm">
      <PageHeader title="Sessions" sub={data ? `${plural(data.n, "session")}${anyFilter ? " (filtered)" : ""}` : loading ? "Loading…" : ""}
        about="The club's joint workouts: who was there, how each did against their current form, and the numbers you ran together."
        tips={[
          "Members = people with a watch file in the session; guests are added by hand on the session page",
          "+2.4 / −1.6 = the session's fitness score (VDOT scale) minus the member's shape the day before; grey = low confidence",
          "Categories are automatic (intervals, tempo, long, easy, race, other); change one on the session page",
          "Cards show the cover photo, else a sketch of everyone's tracks; 🏆 = a club record or PB was set",
          "Click a month on the Club page's chart to see its sessions here",
        ]}>
        {goal && <GoalLink g={goal} />}
        <div class="seg" role="group" aria-label="View">
          <button class={view === "cards" ? "on" : ""} aria-pressed={view === "cards"} onClick={() => setQuery({ view: null })}>▦ Cards</button>
          <button class={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => setQuery({ view: "list" })}>☰ List</button>
        </div>
      </PageHeader>

      {t && (
        <div class="cm-season">
          <div class="cm-season-lbl"><b>{periodLabel}</b><span class="muted">together</span></div>
          <div class="cm-stats">
            <Stat v={t.sessions} label="sessions" />
            <Stat v={Math.round(t.km).toLocaleString("en-GB")} label="km" sub="all members" />
            <Stat v={t.hours.toFixed(t.hours >= 100 ? 0 : 1)} label="hours" sub="moving" />
            <Stat v={t.person_hours_together.toFixed(1)} label="person-hours" sub="side by side" title="Sum over the members of the time spent side by side with someone" />
            {t.reps > 0 && <Stat v={t.reps} label="reps" />}
            {t.photos > 0 && <Stat v={t.photos} label="photos" />}
            {t.guests > 0 && <Stat v={t.guests} label="guest visits" />}
          </div>
          {(data?.h2h ?? []).slice(0, 2).map((h) => <PairCard key={h.a + h.b} h={h} viewer={viewer} />)}
        </div>
      )}

      <div class="cm-filters">
        <div class="chips" role="group" aria-label="Category">
          <button class={"chip" + (!cats.length ? " on" : "")} onClick={() => setQuery({ cat: null })}>All <span class="n">{totalN}</span></button>
          {(data?.categories ?? CATS.map((c): Category => ({ ...c, n: 0 }))).filter((c) => c.n > 0 || cats.includes(c.id)).map((c) => (
            <button key={c.id} class={"chip cat" + (cats.includes(c.id) ? " on" : "")} style={{ "--c": catVar(c.id) } as any} aria-pressed={cats.includes(c.id)}
              title={c.description} onClick={() => setQuery({ cat: toggle(cats, c.id) })}><span aria-hidden="true">{c.emoji}</span>{c.label} <span class="n">{c.n}</span></button>
          ))}
        </div>
        {everyone.length > 1 && <>
          <span class="sep" />
          <div class="chips" role="group" aria-label="People">
            {everyone.map((p) => (
              <button key={p} class={"chip pchip" + (people.includes(p) ? " on" : "")} aria-pressed={people.includes(p)} title={`Sessions with ${personName(p)}`}
                onClick={() => setQuery({ people: toggle(people, p) })}><Av p={p} size="xs" />{personName(p)}</button>
            ))}
          </div>
        </>}
        <span class="sep" />
        <select value={period} onChange={(e) => setQuery({ period: (e.target as HTMLSelectElement).value })} aria-label="Period">
          <option value="">All time</option>
          {per && !periods(years).some((p) => p.id === period) && <option value={period}>{per.label}</option>}
          {periods(years).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <SearchBox value={q} placeholder="Search title, place, workout, note…" />
        {nNear > 0 && (
          <button class={"chip" + (near ? " on" : "")} aria-pressed={near} onClick={() => setQuery({ near: near ? null : 1 })}
            title="Same place and time but different workouts (e.g. only a shared warm-up): hidden by default">Near-misses <span class="n">{nNear}</span></button>
        )}
        {anyFilter ? <button class="link" onClick={() => setQuery({ cat: null, people: null, period: null, q: null, near: null })}>Clear</button> : null}
      </div>

      {error && <div class="err">{error}</div>}
      {data?.store_error && <div class="err">community.json could not be read: {data.store_error}</div>}
      {data && !groups.length && (
        <div class="empty-state">
          <p>{all.data?.groups.length ? "No sessions match these filters." : "No joint sessions yet. They appear once two members' watch files show the same workout (scripts/together.py build)."}</p>
          {anyFilter ? <button onClick={() => setQuery({ cat: null, people: null, period: null, q: null, near: null })}>Clear filters</button> : null}
        </div>
      )}
      <div class={"cm-feed" + (view === "list" ? " list" : "")}>
      {months.map(({ month, rows }) => {
        const km = rows.reduce((s, g) => s + (g.collective?.km ?? 0), 0), h = rows.reduce((s, g) => s + (g.collective?.hours ?? 0), 0);
        return (
          <section class="cm-month" key={month} style={{ "--n": Math.min(rows.length, 4) } as any}>
            <div class="cm-month-head">
              <h2>{monthLabel(month)}</h2>
              <span class="muted">{plural(rows.length, "session")} · {kms(km)} · {hrs(h)}</span>
              <span class="cm-month-cats">{CATS.map((c) => { const n = rows.filter((g) => g.category === c.id).length; return n ? <span title={`${n} ${c.label}`} style={{ "--c": catVar(c.id) } as any}>{c.emoji}{n > 1 ? <sub>{n}</sub> : null}</span> : null; })}</span>
            </div>
            {view === "cards"
              ? <div class="cm-cards">{rows.map((g) => <Card key={g.gid} g={g} tracks={tracks} order={order} />)}</div>
              : <div class="cm-rows" role="list">{rows.map((g) => <Row key={g.gid} g={g} order={order} />)}</div>}
          </section>
        );
      })}
      </div>
    </main>
  );
}
