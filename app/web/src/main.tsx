import { Fragment, options, render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { get, useApi, type ActivityDetail, type Meta } from "./lib/api";
import { defaultPerson, interceptLinks, match, navigate, people, setPeople, useLocation, withPerson } from "./lib/router";
import { MapPage } from "./pages/MapPage";
import { ActivitiesPage } from "./pages/ActivitiesPage";
import { ActivityPage } from "./pages/ActivityPage";
import { DayPage } from "./pages/DayPage";
import { PlacesPage } from "./pages/PlacesPage";
import { HomePage } from "./pages/HomePage";
import { FitnessPage } from "./pages/FitnessPage";
import { ComparePage } from "./pages/ComparePage";
import { TogetherSession } from "./pages/TogetherSession";
import { CommunityPage } from "./pages/CommunityPage";
import { ClubPage } from "./pages/ClubPage";
import { MemberPage, MembersPage } from "./pages/MemberPage";
import { personColour, personName, setPeopleInfo, type PersonInfo } from "./lib/people";
import { addDays, isoLocal } from "./lib/format";
import { setPref, useTheme, useThemePref, type ThemePref } from "./lib/theme";
import "./styles.css";

// Route table. `redirect` entries keep old URLs working (same query).
const ROUTES: { path: string; nav?: string; render: (p: Record<string, string>) => preact.JSX.Element | null }[] = [
  { path: "/", nav: "home", render: () => <HomePage /> },
  { path: "/map", nav: "map", render: () => <MapPage /> },
  { path: "/day", nav: "day", render: () => <DayPage date={null} /> },
  { path: "/day/:date", nav: "day", render: (p) => <DayPage date={p.date} /> },
  { path: "/activities", nav: "activities", render: () => <ActivitiesPage /> },
  { path: "/activities/:id", nav: "activity", render: (p) => <ActivityPage id={p.id} /> },
  { path: "/places", nav: "places", render: () => <PlacesPage /> },
  // Find & group was merged into Activities: same filters, view=groups, gk, g, pick
  { path: "/search", nav: "activities", render: () => redirect("/activities") },
  { path: "/compare", nav: "compare", render: () => <ComparePage /> },
  { path: "/fitness", nav: "fitness", render: () => <FitnessPage /> },
  // Community (the running club): sessions feed, club dashboard, members; a session = the Together session page
  { path: "/community", nav: "community", render: () => <CommunityPage /> },
  { path: "/community/club", nav: "club", render: () => <ClubPage /> },
  { path: "/community/members", nav: "members", render: () => <MembersPage /> },
  { path: "/community/members/:person", nav: "member", render: (p) => <MemberPage person={p.person} /> },
  // Wrapped was removed: its URLs go to the Club page
  { path: "/community/wrapped", nav: "club", render: () => redirect("/community/club") },
  { path: "/community/wrapped/:year", nav: "club", render: () => redirect("/community/club") },
  { path: "/community/:gid", nav: "community", render: (p) => <TogetherSession gid={p.gid} /> },
  // Together became Community · Sessions
  { path: "/together", nav: "community", render: () => redirect("/community") },
  { path: "/together/:gid", nav: "community", render: (p) => redirect(`/community/${encodeURIComponent(p.gid)}`) },
];

/** sections whose paths mean the same for every person (kept on a person switch): a date, a group, the club */
const GLOBAL_SECTIONS = ["day", "together", "community"];

/** replace the URL with `path` + the current query (after render, so the router picks it up) */
function Redirect({ to }: { to: string }) {
  useEffect(() => { navigate(to + location.search, true); }, [to]);
  return null;
}
const redirect = (to: string) => <Redirect to={to} />;


/** coloured initial circle of a person (their fixed colour, lib/people) */
const Avatar = ({ p }: { p: string }) => (
  <span class="avatar" style={{ background: personColour(p) }} aria-hidden="true">{personName(p)[0]}</span>
);

interface TGroupLite { gid: string; participants: { person: string; activity_id: string; role: string }[] }

/** Where a switch to `to` lands: the same section, but ids and filters are per person, so they are dropped — except
 *  /day/<date> and /community/… (a date / a group / the club mean the same for everyone) and an activity done together
 *  (/activities/<id> with `together`): that opens the other person's activity of the same Together group. */
async function switchTarget(path: string, from: string, to: string): Promise<string> {
  const seg = path.split("/")[1] ?? "";
  if (GLOBAL_SECTIONS.includes(seg)) return path;
  const m = /^\/activities\/([^/]+)$/.exec(path);
  if (m) {
    try {
      const d = await get<ActivityDetail>(`/api/${encodeURIComponent(from)}/activities/${m[1]}`);
      const gid = d.activity.together?.gid;
      if (gid) {
        const L = await get<{ groups: TGroupLite[] }>(`/api/${encodeURIComponent(from)}/together?near=1`);
        const g = L.groups.find((x) => x.gid === gid);
        const p = g?.participants.find((x) => x.person === to && x.role !== "other") ?? g?.participants.find((x) => x.person === to);
        if (p) return `/activities/${p.activity_id}`;
        if (g) return `/community/${gid}`;
      }
    } catch { /* fall back to the section */ }
  }
  return seg ? "/" + seg : "/";
}

/** The person at the top of the sidebar (coloured initial + name); with more than one person a menu to switch. */
function PersonMenu({ person, path }: { person: string; path: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const all = people();
  if (all.length < 2) return <div class="who"><Avatar p={person} />{personName(person)}</div>;
  const simple = (p: string) => { const seg = path.split("/")[1] ?? ""; return withPerson(GLOBAL_SECTIONS.includes(seg) ? path : seg ? "/" + seg : "/", p); };
  const go = async (e: MouseEvent, p: string) => {
    e.preventDefault();
    setOpen(false);
    if (p === person) return;
    navigate(withPerson(await switchTarget(path, person, p), p));
  };
  const onActivity = /^\/activities\/[^/]+$/.test(path);
  return (
    <div class="who menu" ref={ref}>
      <button class="who-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}
        title={`Viewing ${personName(person)}'s data — click to switch person`}>
        <Avatar p={person} /><span class="nm">{personName(person)}</span><span class="caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div class="who-list" role="menu">
          <div class="who-hint">{onActivity ? "Switch person (a joint session opens their activity)" : "Switch person"}</div>
          {all.map((p) => (
            <a role="menuitem" href={simple(p)} class={p === person ? "on" : ""} aria-current={p === person}
               onClick={(e) => go(e as unknown as MouseEvent, p)}><Avatar p={p} />{personName(p)}</a>
          ))}
          {/* phones: the theme toggle lives here instead of taking its own row in the top bar */}
          <div class="who-theme"><span class="who-hint">Theme</span><ThemeToggle /></div>
        </div>
      )}
    </div>
  );
}

/** club sessions with this person in the last 30 days (the Community · Sessions badge) */
function useTogetherCount(person: string): number | null {
  const on = people().length > 1;
  const r = useApi<{ n: number }>(on ? `/api/community/sessions?person=${encodeURIComponent(person)}&from=${addDays(isoLocal(), -29)}` : null);
  return r.data?.n ?? null;
}

function Nav({ current, meta, activityId, person, path }:
  { current: string; meta: Meta | null; activityId: string | null; person: string; path: string }) {
  const tab = (id: string, href: string, label: string, soon = false, badge?: number | null, badgeTitle?: string) => (
    <a class={"tab" + (current === id ? " current" : "")} href={href}>
      {label}{soon && <span class="soon">soon</span>}{badge ? <span class="badge" title={badgeTitle}>{badge}</span> : null}
    </a>
  );
  const nTogether = useTogetherCount(person);
  const club = people().length > 1;
  const mm = /^\/community\/members\/([^/]+)$/.exec(path);
  const memberName = mm ? personName(decodeURIComponent(mm[1])) : null;
  return (
    <nav class="side" style={{ "--person": personColour(person) } as any}>
      <PersonMenu person={person} path={path} />
      {club && <span class="group" title={`${personName(person)}'s own training`}>Personal</span>}
      <a class={"step home" + (current === "home" ? " current" : "")} href="/" title="Home: your latest training at a glance">Home</a>
      <span class="step">Explore</span>
      {tab("activities", "/activities", "Activities")}
      {current === "activity" && activityId && <a class="tab sub current" href={`/activities/${activityId}`}>Activity</a>}
      {tab("day", "/day", "Calendar & day")}
      {tab("map", "/map", "Map")}
      {tab("places", "/places", "Places & routes")}
      <span class="step">Analysis</span>
      {tab("compare", "/compare", "Compare")}
      {tab("fitness", "/fitness", "Fitness")}
      {club && <>
        <span class="group community" title="The running club: joint sessions, the club dashboard, members">Community</span>
        {tab("community", "/community", "Sessions", false, nTogether, `${nTogether ?? 0} club sessions with you in the last 30 days`)}
        {tab("club", "/community/club", "Club")}
        {tab("members", "/community/members", "Members")}
        {current === "member" && memberName && <a class="tab sub current" href={path}>{memberName}</a>}
      </>}
      <div class="foot">
        {meta && (
          <div class="built">
            {meta.n_activities} activities, {meta.n_tracks} with GPS<br />
            {meta.first} to {meta.last}
          </div>
        )}
        <ThemeToggle />
      </div>
    </nav>
  );
}

function ThemeToggle() {
  const pref = useThemePref();
  const opt = (p: ThemePref, label: string) => (
    <button class={pref === p ? "on" : ""} onClick={() => setPref(p)} aria-pressed={pref === p}>{label}</button>
  );
  return (
    <div class="theme" title="Colour theme">
      <div class="seg">{opt("auto", "Auto")}{opt("light", "Light")}{opt("dark", "Dark")}</div>
    </div>
  );
}

let lastActivity: { person: string; id: string } | null = null;

function App() {
  const loc = useLocation();
  useTheme(); // re-render on theme change: inline colours read via cssVar() (dots, swatches) pick up the new tokens
  const meta = useApi<Meta>(loc.person ? "/api/meta" : null).data;
  // old / unprefixed URLs (/activities/123, bookmarks from before /<person>/) open the default person
  useEffect(() => { if (!loc.person) navigate(withPerson(loc.path + location.search, defaultPerson()), true); }, [loc.person]);
  if (!loc.person) return null;
  const person = loc.person;
  let hit: { r: (typeof ROUTES)[number]; p: Record<string, string> } | null = null;
  for (const r of ROUTES) {
    const p = match(r.path, loc.path);
    if (p) { hit = { r, p }; break; }
  }
  if (hit?.r.nav === "activity") lastActivity = { person, id: hit.p.id };
  const page = hit ? hit.r.render(hit.p) : <main><h1>Page not found</h1><p class="note">There is no page at <span class="mono">{loc.path}</span>. <a href="/">Go to Home</a>.</p></main>;
  return (
    <div class="app">
      <Nav current={hit?.r.nav ?? ""} meta={meta} activityId={lastActivity?.person === person ? lastActivity.id : null}
           person={person} path={loc.path} />
      {/* keyed on the person: a switch remounts the page, so no filters / selections / maps leak across people */}
      <Fragment key={person}>{page}</Fragment>
    </div>
  );
}

// App code writes person-less hrefs ("/activities/1"); prefix them at render time so the real <a href> is right
// for new-tab / copy-link too (navigate() and API calls do the same, see lib/router.ts and lib/api.ts).
const prevVnode = options.vnode;
options.vnode = (vnode) => {
  const props = vnode.props as { href?: unknown };
  if (vnode.type === "a" && typeof props.href === "string") props.href = withPerson(props.href);
  prevVnode?.(vnode);
};

fetch("/api/people")
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
  .catch(() => ({ default: "adrian", people: ["adrian"] }))   // API down: still render, pages show their own errors
  .then((j: { default: string; people: string[]; info?: Record<string, PersonInfo> }) => {
    setPeople(j.people.length ? j.people : [j.default], j.default);
    setPeopleInfo(j.info);
    interceptLinks();
    render(<App />, document.getElementById("app")!);
  });
