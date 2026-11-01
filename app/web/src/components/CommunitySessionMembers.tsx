// Community part of a session page under the hero: "Who was there" (a card per member in their colour: role,
// workout, rank, km, time, pace, reps, the form chip "+2.4 vs form", records set; links to the activity / Compare;
// guests added by hand as chips), the collective panel ("Together you ran 17.9 km — that's …"), records set in the
// session, "Last time here" and the other sessions at the same place; per member badges (best rep, faster recovery). Data: /api/community/sessions/{gid} (lib/communitySession.ts)
// plus the together session already on the page (participants, leaderboard, race result).
import { useState } from "preact/hooks";
import { withPerson } from "../lib/router";
import { dur, km, num, secs, shortDate, speedText } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { MEDAL, paceText, repTime, type TSession } from "../lib/together";
import {
  climbFun, distanceFun, hoursMin, patchSession, useCommunity,
  type CsForm, type CsMember, type CsRecord, type CsSession, type Facts,
} from "../lib/communitySession";
import { Av } from "./CommunityBits";
import { LastTimeHere } from "./CommunitySessionHighlights";
import "../pages/community-session.css";

const DISC: Record<string, string> = { interval: "interval", long: "long-distance", ski: "ski" };

/** "+2.4 vs form" chip: session score minus the shape the day before (VDOT points) */
export function FormChip({ f, big }: { f: CsForm | null | undefined; big?: boolean }) {
  if (!f) return <span class="cs-form none" title="No form score for this session (easy runs, hikes, rides and multi-sport files have none)">— <small>form</small></span>;
  if (f.delta === null || f.delta === undefined) return <span class="cs-form none" title={f.text}>— <small>form</small></span>;
  const unreliable = f.confidence === "none";
  const d = f.delta;
  const cls = unreliable ? "unrel" : f.confidence === "low" ? "low" : d >= 0.5 ? "up" : d <= -0.5 ? "down" : "flat";
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  const tip = [
    `Session score ${f.score?.toFixed(1)} vs shape ${f.shape?.toFixed(1)} the day before (${DISC[f.discipline ?? ""] ?? f.discipline ?? ""} fitness, VDOT points)`,
    `${sign}${Math.abs(d).toFixed(1)}: ${d >= 0.5 ? "better than current form" : d <= -0.5 ? "below current form" : "right on current form"}`,
    `confidence: ${f.confidence}${f.confidence === "low" ? " (the shape estimate is uncertain)" : ""}`,
    unreliable ? "score unreliable: far off and rejected by the fitness model itself (e.g. HR not maximal, a mislabelled session)" : null,
    f.flags?.length ? `flags: ${f.flags.join(", ").replace(/_/g, " ")}` : null,
  ].filter(Boolean).join("\n");
  return (
    <span class={`cs-form ${cls}` + (big ? " big" : "")} title={tip}>
      {unreliable ? <>? <small>score unreliable</small></> : <><b>{sign}{Math.abs(d).toFixed(1)}</b> <small>vs form</small></>}
    </span>
  );
}

function recordBadge(r: CsRecord) {
  return r.kind === "club_best" ? "🏆 Club record" : "⭐ Club PB";
}

function MemberCard({ m, s, c, f }: { m: CsMember; s: TSession; c: CsSession; f: Facts }) {
  useTheme();
  const p = s.participants.find((x) => x.key === m.key);
  const lb = s.intervals?.leaderboard.find((l) => l.person === m.key);
  const race = s.continuous?.race?.find((r) => r.person === m.key);
  const n = m.numbers;
  const sport = p?.sport && p.sport !== "other" ? p.sport : s.group.sport;   // a multisport file ("other") in a run session: run pace
  const rank = m.rank ?? race?.rank ?? null;
  const ofPeople = s.intervals ? s.intervals.leaderboard.filter((l) => l.total_s !== null).length : s.continuous?.race?.length ?? 0;
  const iv = !!s.intervals && m.reps_done !== null;
  const movingKm = n?.km && n?.moving_s ? (n.km * 1000) / n.moving_s : null;
  const roleText = m.role === "partial" ? "part of the workout" : m.role === "other" ? "other workout" : null;
  const me = m.key === c.viewer;
  const pf = f.people[m.key];
  const badges: { t: string; tip: string }[] = [];
  if (pf?.bestRep && f.sameTarget) badges.push({ t: `🏎️ Best rep R${pf.bestRep.label} · ${repTime(pf.bestRep.s)}`, tip: `${personName(m.key)}'s fastest rep (suspect reps left out)` });
  if (pf && pf.breaks >= 2 && pf.recoveries > 0) badges.push({ t: `💚 Faster recovery ${pf.recoveries}/${pf.breaks}`, tip: `the bigger heart-rate drop in the first 30 s of ${pf.recoveries} of ${pf.breaks} breaks` });
  if (pf?.strongFinish !== null && pf?.strongFinish !== undefined && pf.strongFinish < -0.5) badges.push({ t: "📈 Finished stronger", tip: `the last 3 reps ${Math.abs(pf.strongFinish).toFixed(1)} s faster per rep than the first 3` });
  const stats: [string, string, string?][] = [];
  if (n?.km) stats.push([`${km(n.km)} km`, "distance"]);
  if (n?.moving_s && !(race && Math.abs(race.time_s - n.moving_s) < 90)) stats.push([dur(n.moving_s), "moving", n.dur_s ? `moving time (elapsed ${dur(n.dur_s)})` : "moving time"]);
  if (iv) {
    stats.push([`${m.reps_done}/${m.of}`, "reps", "shared reps done of the shared reps"]);
    if (m.avg_s) stats.push([repTime(m.avg_s), "avg rep", "average time per shared rep"]);
    if (m.pace_s_km) stats.push([paceText(m.pace_s_km, sport), "rep pace", "average pace over the shared reps"]);
  } else if (race) {
    stats.push([dur(race.time_s), "race time"]);
    if (race.dist_km) stats.push([speedText(sport, (race.dist_km * 1000) / race.time_s), "race pace"]);
  } else if (movingKm) stats.push([speedText(sport, movingKm), "avg pace", "distance ÷ moving time"]);
  if (n?.avg_hr) stats.push([`${Math.round(n.avg_hr)}`, "avg HR", "average heart rate, bpm"]);
  return (
    <div class={"cs-member" + (m.role === "other" ? " other" : "") + (me ? " me" : "")} style={{ "--pc": personColour(m.key) } as any}>
      <div class="cs-m-top">
        <Av p={m.key} role={m.role} size="lg" />
        <div class="cs-m-name">
          <span><b>{personName(m.key)}</b>{me && <span class="muted"> · you</span>}</span>
          <span class="cs-m-wk" title={m.type ?? undefined}>{m.workout ?? "—"}{roleText ? <span class="cs-role"> · {roleText}</span> : null}</span>
        </div>
        {rank !== null && ofPeople > 1 && (
          <span class="cs-rank" title={iv ? `Rank over the shared reps${m.rep_wins !== null ? ` · ${m.rep_wins} rep${m.rep_wins === 1 ? "" : "s"} won` : ""}` : "Race placing"}>
            {MEDAL[rank - 1] ?? `#${rank}`}{iv && m.rep_wins ? <small>{m.rep_wins} won</small> : null}
          </span>
        )}
      </div>
      <div class="cs-m-stats">
        {stats.map(([v, l, t]) => <div key={l} title={t}><b>{v}</b><span>{l}</span></div>)}
      </div>
      <div class="cs-m-foot">
        <FormChip f={m.form} />
        {lb?.avg_effort_pct ? <span class="muted small" title={`average peak HR per rep as % of ${lb.effort_mode === "hrr" ? "HR reserve" : "max HR"}`}>effort {lb.avg_effort_pct} %</span> : null}
        <span class="cs-m-links">
          <a href={withPerson(`/activities/${m.activity_id}`, m.person)} title={`${personName(m.key)}'s activity`}>activity ↗</a>
          {m.has_fit && <a href={withPerson(`/compare?suggest=${m.activity_id}`, m.person)} title={`${personName(m.key)}'s own similar sessions, in Compare`}>compare ↗</a>}
        </span>
      </div>
      {(m.records.length > 0 || badges.length > 0) && (
        <div class="cs-m-recs">
          {m.records.map((r, i) => <span key={i} class={"cs-rec-badge " + r.kind} title={r.text}>{recordBadge(r)} · {r.label}</span>)}
          {badges.map((b) => <span key={b.t} class="cs-rec-badge hl" title={b.tip}>{b.t}</span>)}
        </div>
      )}
    </div>
  );
}

function Guests({ c }: { c: CsSession }) {
  const [v, setV] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const g = c.meta.guests;
  const save = async (list: string[]) => {
    setBusy(true); setErr(null);
    try { await patchSession(c.gid, { guests: list }); setV(""); } catch (e: any) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  const add = () => { const names = v.split(",").map((x) => x.trim()).filter(Boolean); if (names.length) save([...g, ...names]); };
  return (
    <div class="cs-guests">
      {g.map((name) => (
        <span key={name} class="cs-guest" title={`${name}: came along (no watch file here)`}>
          <span class="cs-guest-av" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>{name}
          <button class="cs-x" aria-label={`Remove ${name}`} title={`Remove ${name}`} disabled={busy} onClick={() => save(g.filter((x) => x !== name))}>×</button>
        </span>
      ))}
      {open ? (
        <span class="cs-guest-add">
          <input value={v} placeholder="Name(s), comma separated" aria-label="Guest name" maxLength={200} disabled={busy} autoFocus
            onInput={(e) => setV((e.target as HTMLInputElement).value)}
            onKeyDown={(e) => { if (e.key === "Enter") add(); if (e.key === "Escape") { setOpen(false); setV(""); } }} />
          <button onClick={add} disabled={busy || !v.trim()}>Add</button>
          <button class="link" onClick={() => { setOpen(false); setV(""); }}>Done</button>
        </span>
      ) : (
        <button class="cs-guest-btn" onClick={() => setOpen(true)} title="Someone ran with you without a watch file here? Add them as a guest">＋ guest</button>
      )}
      {err && <span class="cs-err small">{err}</span>}
    </div>
  );
}

const SPORT_VERB: Record<string, string> = { run: "ran", hike: "hiked", bike: "rode", ski: "skied", swim: "swam" };

function Collective({ c, s }: { c: CsSession; s: TSession }) {
  const k = c.collective;
  const verb = SPORT_VERB[s.group.sport] ?? "covered";
  const fun = distanceFun(k.km);
  const climb = climbFun(k.climb_m);
  const n = Math.max(1, k.n_members);
  const buns = k.kcal ? Math.round(k.kcal / 250) : 0;
  const tiles: { v: string; l: string; sub?: string | null; tip?: string }[] = [
    { v: `${km(k.km)} km`, l: `${verb} together`, sub: n > 1 && k.km ? `${km(k.km / n)} km each on average` : null, tip: "sum of everyone's distance" },
    { v: hoursMin(k.moving_s), l: `collective time ${s.group.sport === "run" ? "running" : "moving"}`, sub: k.moving_s ? `${dur(k.moving_s / n)} each` : null, tip: "sum of everyone's moving time" },
  ];
  if (k.reps) tiles.push({ v: num(k.reps), l: "reps between you", sub: k.reps_shared ? `${k.reps_shared} side by side in the same slots` : null, tip: "every full rep each person ran" });
  if (k.person_min_shared) tiles.push({ v: `${num(k.person_min_shared)}`, l: "person-minutes together",
    sub: k.person_min_together ? `${num(k.person_min_together)} of them side by side` : null,
    tip: `per person the time out there at the same time as the others, summed${k.together_estimated ? " (estimated: no track on one side)" : ""}; "side by side" = within a few metres of each other` });
  if (k.climb_m) tiles.push({ v: `${num(k.climb_m)} m`, l: "climbed", sub: climb, tip: "sum of everyone's elevation gain" });
  if (k.kcal) tiles.push({ v: num(k.kcal), l: "kcal burned", sub: buns >= 1 ? `≈ ${buns} cinnamon bun${buns === 1 ? "" : "s"} 🥐` : null, tip: "sum of the watches' calorie estimates (a bun ≈ 250 kcal)" });
  return (
    <div class="cs-coll card">
      <p class="cs-coll-lead">
        Together you {verb} <b>{km(k.km)} km</b>{fun ? <> — {fun}</> : null}{k.moving_s ? <>, <b>{hoursMin(k.moving_s)}</b> of {s.group.sport === "run" ? "running" : "moving"} between {n === 2 ? "the two of you" : `the ${n} of you`}</> : null}.
      </p>
      <div class="cs-tiles">
        {tiles.map((t) => <div key={t.l} title={t.tip}><b>{t.v}</b><span>{t.l}</span>{t.sub && <small>{t.sub}</small>}</div>)}
      </div>
    </div>
  );
}

function Records({ c, base }: { c: CsSession; base: string }) {
  if (!c.records.length) return null;
  return (
    <div class="card cs-recs">
      <div class="head sm"><h2>Records set</h2><span class="sub">club records and club PBs from this session</span></div>
      <ul>
        {c.records.map((r, i) => (
          <li key={i}>
            <span class={"cs-rec-badge " + r.kind}>{recordBadge(r)}</span>
            <span class="pn"><Av p={r.key ?? r.person} size="sm" /><b>{personName(r.key ?? r.person)}</b></span>
            <span>{r.measure === "avg" ? "average rep" : r.measure === "race" ? "race" : "fastest rep"} {r.label}: <b>{r.measure === "race" ? dur(r.value_s) : repTime(r.value_s)}</b></span>
            {r.improvement_s ? <span class="good small">{secs(r.improvement_s, 1)} faster</span> : null}
            {r.prev_s ? <span class="muted small">than {r.prev_person && r.prev_person !== r.person ? `${personName(r.prev_person)}'s ` : "the old "}{r.measure === "race" ? dur(r.prev_s) : repTime(r.prev_s)}
              {r.prev_gid ? <> (<a href={`${base}/${encodeURIComponent(r.prev_gid)}`}>{r.prev_date ? shortDate(r.prev_date, c.date) : "earlier"}</a>)</> : null}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SamePlace({ c, base }: { c: CsSession; base: string }) {
  if (!c.same_place.length) return null;
  const emoji = (id: string) => c.categories.find((x) => x.id === id)?.emoji ?? "";
  const place = c.place?.name ?? "this place";
  return (
    <div class="card cs-same">
      <div class="head sm"><h2>Same place before</h2><span class="sub">{c.same_place.length} other session{c.same_place.length === 1 ? "" : "s"} at {place}</span></div>
      <ul>
        {c.same_place.map((x) => (
          <li key={x.gid}>
            <a href={`${base}/${encodeURIComponent(x.gid)}`}>
              <span class="cs-sp-date">{shortDate(x.date, c.date)}</span>
              <span class="cs-sp-wk">{emoji(x.category)} {x.workout}</span>
              <span class="cs-sp-ppl">{x.people.map((p) => <Av key={p} p={p} size="sm" />)}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CommunitySessionMembers({ gid, s, f }: { gid: string; s: TSession; f: Facts }) {
  const { data: c } = useCommunity(gid);
  const base = "/community";
  if (!c) return null;
  const order = s.participants.map((p) => p.key);
  const members = [...c.members].sort((a, b) => (order.indexOf(a.key) + 1 || 99) - (order.indexOf(b.key) + 1 || 99));
  const nT = c.n_timed, nG = c.n_guests;
  return (
    <>
      <section class="cs-who">
        <div class="head"><h2>Who was there</h2>
          <span class="sub">{nT} member{nT === 1 ? "" : "s"} timed{nG ? ` · ${nG} guest${nG === 1 ? "" : "s"}` : ""}{c.form_available ? " · form = this session vs each person's shape the day before" : ""}</span></div>
        <div class="cs-members">
          {members.map((m) => <MemberCard key={m.key} m={m} s={s} c={c} f={f} />)}
        </div>
        <Guests c={c} />
      </section>
      <div class="cs-grid">
        <Collective c={c} s={s} />
        {(c.records.length > 0 || c.same_place.length > 0) && (
          <div class="cs-side">
            <Records c={c} base={base} />
            <LastTimeHere c={c} base={base} />
            <SamePlace c={c} base={base} />
          </div>
        )}
      </div>
    </>
  );
}
