// One club session (/community/<gid>; /api/together/<gid> + the club layer /api/community/sessions/<gid>). Top to
// bottom: a plain title line with small links and an Edit menu, the note / story, one table row per person, then
// for intervals "Rep by rep" (a row per rep, a column per person) and the splits; for continuous runs / races the
// race result and km splits. Everything else is folded under "More detail" (rendered only when open): leaderboard,
// rep close-up (metres behind the rep's leader + the track), rep pace, replay, breaks, heart rate, photos; for
// continuous runs the gap over distance, distance apart, who led where and the map.
// URL: ?more=1 (open "More detail") &rep=<slot label>&align=clock&replay=1&rrep=<rep to replay>
import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { useApi } from "../lib/api";
import { people as allPeople, currentPerson, setQuery, useLocation, withPerson } from "../lib/router";
import { cssVar, dur, km, repLabel, secs, shortDate, speedLabel, speedText } from "../lib/format";
import { useTheme } from "../lib/theme";
import { mapColour, personColour, personInfo, personName, setPeopleInfo } from "../lib/people";
import {
  behind, paceText, pathBetween, posAt, repTime, rollingMedian, valAt,
  type Cell, type Closeup, type Intervals, type Replay, type TParticipant, type TSession,
} from "../lib/together";
import { TLine, type TSeries } from "../components/TLine";
import { TogetherMap, type MapDot, type MapLine } from "../components/TogetherMap";
import { ReplayPanel } from "../components/Replay";
import { CommunitySessionHero } from "../components/CommunitySessionHero";
import { CommunitySessionMembers, SamePlace } from "../components/CommunitySessionMembers";
import { CommunitySessionSplits } from "../components/CommunitySessionSplits";
import { CommunitySessionPhotos } from "../components/CommunitySessionPhotos";
import { closestRep, sessionFacts, useCommunity } from "../lib/communitySession";
import "./together.css";

type N = number | null;

/** y tick for "behind" charts (values plotted as negatives: the leader / front on top) */
const behindTick = (v: number) => (v > -0.5 ? (v > 0.5 ? "" : "level") : `${secs(-v)} behind`);
const behindTickM = (v: number) => (v > -0.5 ? (v > 0.5 ? "" : "level") : `${Math.round(-v)} m behind`);
const neg = (v: N) => (v === null ? null : -v);
/** rep label: "Rep 3" (room) / "R3" (compact); extra slots "3+1" -> "R3+1" */
const rl = (l: string, compact = true) => repLabel(l, compact);

/** the last non-null value at or before index i (a line freezes at its finish) */
function lastAt(v: N[], i: number): N {
  for (let j = Math.min(i, v.length - 1); j >= 0; j--) if (v[j] !== null) return v[j];
  return null;
}

const effortLabel = (mode: string | null | undefined) => (mode === "hrr" ? "HRR" : "max HR");
/** HR as % of max (or of reserve) for one participant */
function hrPct(p: TParticipant | undefined, hr: N): N {
  const n = p?.hr_norm;
  if (hr === null || !n?.max) return null;
  if (n.mode === "hrr" && n.rest) return ((hr - n.rest) / (n.max - n.rest)) * 100;
  return (hr / n.max) * 100;
}

// ------------------------------------------------------------------ header
function Caveats({ s }: { s: TSession }) {
  const by: Record<string, typeof s.caveats> = {};
  // summary-only sessions: only what explains the missing track
  for (const c of s.caveats) if (!s.group.no_track || /no_/.test(c.kind)) (by[c.kind] ??= []).push(c);
  const who = (cs: typeof s.caveats) => cs.map((c) => (c.person ? personInfo(c.person).name : "")).filter(Boolean).join(", ");
  const chips: { k: string; text: string; title: string }[] = [];
  // engine texts start with the person id ("patrik: no FIT file"): proper-case the names, plain words
  const plain = (c: (typeof s.caveats)[number]) => {
    let t = c.text;
    for (const p of s.participants) t = t.replace(new RegExp(`\\b${p.person}\\b`, "g"), personInfo(p.person).name);
    return t.replace(/no FIT file \(no track, summary only\)/, "no watch file (summary only)");
  };
  for (const [k, cs] of Object.entries(by)) {
    const title = cs.map(plain).join("\n");
    if (k === "sampling") chips.push({ k, text: `${who(cs)}: ${cs.map((c) => c.text.match(/every ([\d.]+ s)/)?.[1]).filter(Boolean).join(" / ")} recording · times ±${Math.max(...cs.map((c) => Number(c.text.match(/±(\d+)/)?.[1] ?? 1)))} s`, title });
    else if (k === "clock") chips.push({ k, text: `Clock ${cs.map((c) => `${personInfo(c.person ?? "").name} ${c.text.match(/by ([+−-]?[\d.]+ s)/)?.[1] ?? ""}`).join(", ")}`, title: title + "\n(only matters for Clock time and the replay; rep times don't depend on it)" });
    else if (k === "hr") chips.push({ k, text: cs.length === s.participants.length ? "HR max from watch zones" : `${who(cs)}: HR max from watch zones`, title });
    else chips.push({ k, text: cs.map(plain).join("; "), title });
  }
  if (!chips.length) return null;
  return <p class="muted small tg-caveats">Recording: {chips.map((c, i) => <span key={c.k} title={c.title}>{i > 0 && " · "}{c.text}</span>)}</p>;
}

/** the viewer's own (compare-able) activity in this session */
function mineOf(s: TSession): TParticipant | undefined {
  const v = s.viewer ?? currentPerson();
  return s.participants.find((p) => p.person === v && p.role !== "other" && p.has_fit);
}

/** "Compare with my other … sessions" (the viewer's own activity in Compare) */
function CompareLink({ s }: { s: TSession }) {
  const mine = mineOf(s);
  const what = mine ? (mine.signature ?? (mine.workout ?? "").toLowerCase()) : "";
  if (!mine || !what || s.group.no_track) return null;
  return <a href={withPerson(`/compare?suggest=${mine.activity_id}`, mine.person)} title={`${personName(mine.key)}'s other sessions like this one, in Compare`}>Compare with my other {what} sessions</a>;
}

// ------------------------------------------------------------------ intervals
/** "0.7 s slower" / "2.0 s faster" (fade per rep, or per km when the reps differ) */
function fadeText(v: number, perKm: boolean) {
  const a = Math.abs(v).toFixed(1);
  if (Math.abs(v) < 0.05) return "even";
  return `${a} s${perKm ? "/km" : ""} ${v > 0 ? "slower" : "faster"}`;
}

function Leaderboard({ s, iv }: { s: TSession; iv: Intervals }) {
  const sport = s.group.sport;
  const effMode = iv.leaderboard.find((l) => l.effort_mode)?.effort_mode;
  const anyEff = iv.leaderboard.some((l) => l.avg_effort_pct);
  const nT = new Set(iv.leaderboard.flatMap((l) => l.by_target.map((t) => t.text))).size;
  const anySus = iv.leaderboard.some((l) => (l.suspect ?? []).length);
  return (
    <div class="card">
      <div class="head sm"><h2>Leaderboard</h2><span class="sub">ordered by average rep pace over each person's own reps</span></div>
      <div class="tg-mxwrap"><table class="tg-tbl lb">
        <thead><tr>
          <th></th><th>Person</th><th class="num" title="Full reps run (partial reps not counted)">Reps</th>
          <th class="num" title={"Average time per rep" + (nT > 1 ? ", per set distance" : "") + (anySus ? " (suspect reps left out)" : "")}>Avg rep</th>
          <th class="num" title="Average rep pace: total distance ÷ total time of the person's own reps; then the difference to the fastest average">Pace</th>
          <th class="num opt" title="Fastest rep (suspect reps left out)">Best</th>
          <th class="num opt" title={"Fade: average of the last 3 reps minus the first 3 (per rep; per km when the rep distances differ).\nSlower = faded towards the end, faster = finished stronger. Suspect reps are left out."}>Fade</th>
          {anyEff && <th class="num opt" title={`Average peak HR per rep as % of ${effortLabel(effMode)}`}>Effort</th>}
        </tr></thead>
        <tbody>
          {iv.leaderboard.map((l) => {
            const fade = l.fade_s ?? l.fade_s_km;
            return (
              <tr key={l.person}>
                <td class="rank">{l.rank ?? ""}</td>
                <td><span class={l.person === s.viewer ? "me" : ""}>{personName(l.person)}</span></td>
                <td class="num" title={l.n_partial ? `+ ${l.n_partial} partial` : undefined}>{l.reps_done}{l.n_partial ? <span class="muted small"> +{l.n_partial} partial</span> : null}</td>
                <td class="num"><span class="nm2 r">{l.by_target.length ? l.by_target.map((t) => (
                  <span key={t.text}>{t.kind === "dist" ? repTime(t.avg_s) : `${t.avg_m ?? "—"} m`}{nT > 1 || l.by_target.length > 1 ? <span class="muted small"> {t.text} ×{t.n}</span> : null}</span>
                )) : "—"}</span></td>
                <td class="num"><span class="nm2 r"><b>{paceText(l.pace_s_km, sport)}</b>{l.gap_s_km ? <span class="muted small">+{l.gap_s_km.toFixed(1)} s/km</span> : null}</span></td>
                <td class="num opt">{l.best ? <>{repTime(l.best.result_s)} <span class="muted small">{rl(l.best.label)}</span></> : "—"}</td>
                <td class={"num opt " + (fade === null ? "" : fade < -0.5 ? "good" : fade > 0.5 ? "bad" : "")}>{fade === null ? "—" : fadeText(fade, l.fade_s === null)}</td>
                {anyEff && <td class="num opt">{l.avg_effort_pct ? `${l.avg_effort_pct} %` : "—"}</td>}
              </tr>
            );
          })}
        </tbody>
      </table></div>
      {anySus && (
        <p class="note small">? suspect rep: {iv.leaderboard.filter((l) => (l.suspect ?? []).length).map((l) => `${personName(l.person)} ${(l.suspect ?? []).map((x) => rl(x, false)).join(", ")}`).join("; ")} — much faster than the other reps of the same distance (probably short or a lap pressed early); counted as a rep, left out of the averages, Best and Fade.</p>
      )}
    </div>
  );
}

function cellMain(c: Cell, timeSlot: boolean) {
  if (timeSlot) return c.dist_m !== null ? `${Math.round(c.dist_m)} m` : "—";
  return repTime(c.partial ? c.own_time_s ?? c.time_s : c.result_s ?? c.time_s);
}

/** results per rep: a table (a row per rep, a column per person) or a leaderboard per rep (a column per rep, fastest first) */
function RepResults({ s, iv, sel, onSel }: { s: TSession; iv: Intervals; sel: string | null; onSel: (l: string) => void }) {
  const [view, setView] = useState<"table" | "board">(() => localStorage.getItem("session.repView") === "board" ? "board" : "table");
  const pick = (v: "table" | "board") => { setView(v); localStorage.setItem("session.repView", v); };
  return (
    <div class="card tg-rep-results">
      <div class="head sm"><h2>Rep by rep</h2>
        <span class="sub" title="bold = fastest of the rep · +s behind it · = dead heat · * partial (not ranked) · ≈ from the speed step · ? suspect (hover)">
          {view === "board" ? "fastest first in each rep" : "bold = fastest of the rep"}</span>
        <div class="tools"><div class="seg">
          <button class={view === "table" ? "on" : ""} aria-pressed={view === "table"} onClick={() => pick("table")}>Table</button>
          <button class={view === "board" ? "on" : ""} aria-pressed={view === "board"} onClick={() => pick("board")}>Leaderboard</button>
        </div></div>
      </div>
      {view === "board" ? <RepBoards iv={iv} sel={sel} onSel={onSel} /> : <Standings s={s} iv={iv} sel={sel} onSel={onSel} />}
    </div>
  );
}

/** a column per rep; in each, the people fastest first: the leader with the time, the others with +gap (time in the tooltip) */
/** one rep's results, fastest first: ranked people, then those who ran it but aren't ranked (partial, or alone) */
type RepEntry = { person: string; text: string; lead: boolean; ranked: boolean; tip: string };
function repEntries(iv: Intervals, sl: Intervals["slots"][number]): RepEntry[] {
  const st = iv.standings[sl.slot];
  const timeSlot = sl.target?.kind === "time";
  const out: RepEntry[] = st.order.map((o, i) => {
    const c = iv.reps[o.person][sl.slot]!;
    const main = cellMain(c, timeSlot);
    const gap = timeSlot && o.gap_m !== null ? `+${Math.round(o.gap_m)} m` : `+${o.gap_s.toFixed(1)}`;
    const text = (i === 0 ? main : o.tie ? "=" : gap) + (c.suspect ? "?" : "") + (c.source === "onset" ? "≈" : "");
    const tip = [`${o.rank}. ${personName(o.person)} ${main}`, i && !o.tie ? `${gap} s behind` : null, o.tie ? "dead heat" : null,
      c.suspect ? `suspect: ${c.suspect}` : null, c.source === "onset" ? "from the speed step" : null].filter(Boolean).join(" · ");
    return { person: o.person, text, lead: i === 0 && st.order.length >= 2, ranked: true, tip };
  });
  for (const k of Object.keys(iv.reps)) {
    const c = iv.reps[k][sl.slot];
    if (!c || st.order.some((o) => o.person === k)) continue;
    out.push({ person: k, text: cellMain(c, timeSlot) + (c.partial ? "*" : ""), lead: false, ranked: false,
      tip: `${personName(k)} ${cellMain(c, timeSlot)} · ${c.partial ? "partial, not ranked" : "not ranked: only one full result"}` });
  }
  return out;
}

/** a person as a small coloured circle with their initials */
function PersonDot({ k }: { k: string }) {
  return <span class="pdot" style={{ background: personColour(k) }}>{personInfo(k).initials}</span>;
}

function RepCell({ e }: { e: RepEntry | undefined }) {
  if (!e) return <td class="rc" />;
  return <td class={"rc" + (e.ranked ? "" : " unranked")} title={e.tip}><PersonDot k={e.person} /><span class="rv">{e.lead ? <b>{e.text}</b> : e.text}</span></td>;
}

/** Leaderboard: a column per rep, a row per place (1st on top) */
function RepBoards({ iv, sel, onSel }: { iv: Intervals; sel: string | null; onSel: (l: string) => void }) {
  const slots = iv.slots.filter((sl) => !sl.warmup);
  const cols = slots.map((sl) => repEntries(iv, sl));
  const places = Math.max(0, ...cols.map((c) => c.length));
  const click = (sl: Intervals["slots"][number]) => iv.rep_closeups.find((c) => c.slot === sl.label)?.available ? () => onSel(sl.label) : undefined;
  return (
    <div class="tg-mxwrap">
      <table class="tg-tbl reps boards">
        <thead><tr>
          <th></th>
          {slots.map((sl) => <th key={sl.slot} class={(sel === sl.label ? "selected " : "") + (click(sl) ? "clickable" : "")}
            title={sl.target?.text ?? ""} onClick={click(sl)}>{rl(sl.label)}</th>)}
        </tr></thead>
        <tbody>
          {Array.from({ length: places }, (_, p) => (
            <tr key={p}>
              <td class="muted">{p + 1}.</td>
              {cols.map((c, i) => <RepCell key={slots[i].slot} e={c[p]} />)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Table: a row per rep, fastest to the left */
function Standings({ s, iv, sel, onSel }: { s: TSession; iv: Intervals; sel: string | null; onSel: (l: string) => void }) {
  void s;
  const rows = iv.slots.map((sl) => ({ sl, es: repEntries(iv, sl) }));
  const places = Math.max(0, ...rows.map((r) => r.es.length));
  return (
    <div class="tg-mxwrap">
      <table class="tg-tbl reps mx">
        <thead><tr>
          <th>Rep</th><th>Set</th>
          {Array.from({ length: places }, (_, p) => <th key={p} class="muted">{p + 1}.</th>)}
        </tr></thead>
        <tbody>
          {rows.map(({ sl, es }) => {
            const cu = iv.rep_closeups.find((c) => c.slot === sl.label);
            return (
              <tr key={sl.slot} class={(sel === sl.label ? "selected " : "") + (cu?.available ? "clickable " : "") + (!sl.anchor ? "extra" : "") + (sl.warmup ? " warmup" : "")}
                onClick={cu?.available ? () => onSel(sl.label) : undefined}>
                <td class="rl">{rl(sl.label)}</td>
                <td class="muted">{sl.target?.text ?? ""}</td>
                {Array.from({ length: places }, (_, p) => <RepCell key={p} e={es[p]} />)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** each person's own rep times (one set distance) or rep pace across the session (each person's own reps, whoever ran what) */
function RepTrend({ s, iv, sel }: { s: TSession; iv: Intervals; sel: string | null }) {
  const theme = useTheme();
  const keys = Object.keys(iv.reps);
  const full = (c: Cell | null | undefined) => !!c && !c.partial && !c.suspect && !c.warmup && c.result_s !== null;   // suspect: probably short; warm-ups not counted
  const tg = new Set(iv.slots.filter((sl) => keys.some((k) => full(iv.reps[k][sl.slot]))).map((sl) => sl.target?.text));
  const one = tg.size === 1 && iv.slots.find((sl) => keys.some((k) => full(iv.reps[k][sl.slot])))?.target?.kind === "dist";
  const val = (sl: Intervals["slots"][number], c: Cell | null | undefined): N => {
    if (!full(c) || !sl.target) return null;
    if (one) return c!.result_s;
    return sl.target.kind === "dist" ? (c!.result_s! * 1000) / sl.target.value : c!.dist_m ? (c!.time_s! * 1000) / c!.dist_m : null;
  };
  const x = useMemo(() => iv.slots.map((_s, i) => i + 1), [iv]);
  const series = useMemo<TSeries[]>(() => keys.map((k) => ({ key: k, colour: personColour(k), points: true, width: 2,
    values: iv.slots.map((sl) => val(sl, iv.reps[k][sl.slot])) })), [iv, theme]);
  const [ci, setCi] = useState<number | null>(null);
  const selI = sel ? iv.slots.findIndex((sl) => sl.label === sel) : -1;
  if (iv.slots.length < 2) return null;
  const i = ci ?? (selI >= 0 ? selI : null);
  const fmt = (v: number) => (one ? repTime(v) : paceText(v, s.group.sport));
  return (
    <div class="card">
      <div class="head sm"><h2>{one ? "Rep times" : "Rep pace"}</h2><span class="sub">faster on top{one ? "" : " (pace, as the distances differ)"}</span></div>
      <div class="tg-legend">
        {i === null ? <span class="muted">hover a rep</span> : <>
          <span class="muted">{rl(iv.slots[i].label, false)}{iv.slots[i].target ? ` · ${iv.slots[i].target!.text}` : ""}</span>
          {series.filter((sr) => sr.values[i] !== null).sort((a, b) => a.values[i]! - b.values[i]!).map((sr) => (
            <span key={sr.key}><i class="sw" style={{ background: sr.colour }} />{personName(sr.key)} <b>{fmt(sr.values[i]!)}</b></span>))}
        </>}
      </div>
      <TLine x={x} series={series} xLabels={iv.slots.map((sl) => rl(sl.label))} height={190} dir={-1} yFmt={(v) => (one ? repTime(v) : `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")}`)}
        ySize={62} onCursor={setCi} bands={selI >= 0 ? [{ a: selI + 1 - 0.4, b: selI + 1 + 0.4 }] : []} />
    </div>
  );
}

function RepCloseup({ s, iv, cu, replay, align, onSel }: {
  s: TSession; iv: Intervals; cu: Closeup; replay: Replay | null; align: "own" | "clock"; onSel: (l: string) => void;
}) {
  const theme = useTheme();
  const A = (align === "clock" ? cu.clock : cu.own)!;
  const keys = Object.keys(A.people);
  const slot = iv.slots.find((x) => x.label === cu.slot)!;
  const x = useMemo(() => Array.from({ length: A.n }, (_v, i) => A.x0 + i), [A]);
  const series = useMemo<TSeries[]>(() => keys.map((k) => ({ key: k, colour: personColour(k), values: A.people[k].behind_m.map(neg),
    width: k === cu.leader ? 1.5 : 2.4 })), [A, theme]);
  const cells = Object.fromEntries(keys.map((k) => [k, iv.reps[k]?.[slot.slot] ?? null]));
  const starts = keys.map((k) => cells[k]?.clock_start_s).filter((v): v is number => v !== undefined && v !== null);
  const clock0 = Math.min(...starts);
  const leaderCell = cu.leader ? cells[cu.leader] : null;
  const finishX = Math.min(A.x0 + A.n - 1, Math.round(leaderCell?.own_time_s ?? leaderCell?.time_s ?? A.n - 1) + (align === "clock" ? Math.round((leaderCell?.clock_start_s ?? clock0) - clock0) : 0));
  const [ci, setCi] = useState<number | null>(null);
  const xi = ci ?? finishX - A.x0;
  const xv = A.x0 + xi;
  const clockOf = (k: string, xx: number) => (align === "clock" ? clock0 + xx : (cells[k]?.clock_start_s ?? clock0) + xx);
  const lines = useMemo<MapLine[]>(() => replay ? keys.map((k) => {
    const c = cells[k];
    return { key: k, colour: mapColour(k), width: 3,
      coords: c ? pathBetween(replay.people[k], c.clock_start_s, c.clock_start_s + (c.own_time_s ?? c.time_s ?? 60)) : [] };
  }) : [], [replay, cu]);
  const dots: MapDot[] = replay ? keys.map((k) => ({ key: k, colour: mapColour(k), initials: personInfo(k).initials,
    pos: posAt(replay.people[k], clockOf(k, xv)), title: personName(k), strong: k === s.viewer })) : [];
  const avail = iv.rep_closeups.filter((c) => c.available).map((c) => c.slot);
  const pi = avail.indexOf(cu.slot);
  const startDelta = keys.map((k) => ({ k, d: A.people[k].start_delta_s })).filter((v) => Math.abs(v.d) >= 0.1);
  return (
    <div class="card">
      <div class="head sm">
        <h2>Rep close-up</h2>
        <div class="seg reppick" role="group" aria-label="Rep">
          <button disabled={pi <= 0} onClick={() => onSel(avail[pi - 1])} aria-label="Previous rep">Prev</button>
          <button class="on" disabled>{rl(cu.slot, false)}{slot.target ? ` · ${slot.target.text}` : ""}</button>
          <button disabled={pi >= avail.length - 1} onClick={() => onSel(avail[pi + 1])} aria-label="Next rep">Next</button>
        </div>
        <div class="tools">
          <div class="seg" role="group" aria-label="Start alignment">
            <button class={align === "own" ? "on" : ""} onClick={() => setQuery({ align: null })} title="Each person from their own rep start: the gap is running speed only (fair, ±2 s detection)">Own start</button>
            <button class={align === "clock" ? "on" : ""} onClick={() => setQuery({ align: "clock" })} title="Real clock time: a late start counts (the real race)">Clock</button>
          </div>
        </div>
      </div>
      <div class="tg-cu-map">
        {replay ? (
          <TogetherMap lines={lines} dots={dots} fitKey={s.gid + ":" + cu.slot} />
        ) : <div class="map bordered"><div class="msg">Loading track…</div></div>}
      </div>
      <div class="tg-legend">
        <span class="muted">{ci === null ? "at the leader's finish" : `${xv} s into the rep`}</span>
        {keys.map((k) => {
          const b = lastAt(A.people[k].behind_m, xi), d = lastAt(A.people[k].d_m, xi);
          const lead = k === cu.leader || (b !== null && b < 0.5);
          return <span key={k}><i class="sw" style={{ background: personColour(k) }} />{personName(k)} <b>{
            lead ? (ci === null ? "finished" : d !== null ? `${Math.round(d)} m, leads` : "leads") : b === null ? "—" : `${Math.round(b)} m behind`}</b></span>;
        })}
        {align === "clock" && startDelta.length > 0 && <span class="muted">start: {startDelta.map((v) => `${personName(v.k)} ${secs(Math.abs(v.d), 1)} ${v.d > 0 ? "later" : "earlier"}`).join(", ")}</span>}
      </div>
      <TLine x={x} series={series} height={170} zero from0 xFmt={(v) => `${v} s`} yFmt={behindTickM} ySize={82} onCursor={setCi}
        cursorX={ci === null ? finishX : undefined} />
      <p class="note small">metres behind the rep's leader (on top) along the track (each person's distance since their {align === "own" ? "own start" : "start on the clock"}, scaled to {cu.target_m} m)</p>
    </div>
  );
}

function Breaks({ iv }: { iv: Intervals }) {
  const keys = Object.keys(iv.breaks).filter((k) => iv.breaks[k].length);
  const rows = useMemo(() => {
    const by = new Map<number, { after: string; next: string; slot: number }>();
    for (const k of keys) for (const b of iv.breaks[k]) {
      if (!by.has(b.after_slot)) by.set(b.after_slot, { after: b.after_label, next: b.next_label, slot: b.after_slot });
    }
    const all = [...by.values()].sort((a, b) => a.slot - b.slot);
    // with several people, only the breaks at least two of them had (the rest is one person's own session)
    const shared = all.filter((r) => keys.filter((k) => iv.breaks[k].some((b) => b.after_slot === r.slot)).length >= 2);
    return keys.length >= 2 && shared.length ? shared : all;
  }, [iv]);
  if (!rows.length) return null;
  const mode = effortLabel(iv.leaderboard[0]?.effort_mode);
  return (
    <section>
      <div class="head"><h2>Breaks · recovery</h2><span class="sub">HR drop in the first 30 s and HR at the next start (% of {mode})</span></div>
      <div class="tg-mxwrap">
        <table class="tg-tbl br" style={{ width: "auto" }}>
          <thead>
            <tr><th rowSpan={2}>After rep</th>
              {keys.map((k) => <th key={k} colSpan={3} class="pgrp" style={{ borderBottomColor: personColour(k) }}>{personName(k)}</th>)}
              <th rowSpan={2} title="Who's HR dropped more in the first 30 s">Faster recovery</th></tr>
            <tr>{keys.map((k) => [<th key={k + "d"} class="num">Break</th>, <th key={k + "h"} class="num" title="HR drop in the first 30 s of the break">HR drop 30 s</th>, <th key={k + "n"} class="num">Next start</th>])}</tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const rf = iv.recovered_faster.find((x) => x.after_slot === r.slot);
              return (
                <tr key={r.slot}>
                  <td>{rl(r.after)} → {rl(r.next)}</td>
                  {keys.map((k) => {
                    const b = iv.breaks[k].find((x) => x.after_slot === r.slot);
                    if (!b) return [<td key={k + "d"} class="num muted">—</td>, <td key={k + "h"} />, <td key={k + "n"} />];
                    return [
                      <td key={k + "d"} class="num" title={b.kind}>{dur(b.dur_s)}</td>,
                      <td key={k + "h"} class={"num" + (rf?.person === k ? " best" : "")} title={b.drop_30 !== null ? `${b.hr_peak} → ${b.hr_30} bpm` : "break shorter than 30 s"}>
                        {b.drop_30_pct !== null ? `${Math.abs(b.drop_30_pct)} %` : "—"}{b.drop_30 !== null && <span class="muted small"> ({Math.abs(b.drop_30)} bpm)</span>}</td>,
                      <td key={k + "n"} class="num" title={b.hr_next_start ? `${b.hr_next_start} bpm` : ""}>{b.hr_next_start_pct !== null ? `${b.hr_next_start_pct} %` : "—"}</td>,
                    ];
                  })}
                  <td>{rf ? personName(rf.person) : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function HeartIntervals({ s, iv, cu, replay }: { s: TSession; iv: Intervals; cu: Closeup | null; replay: Replay | null }) {
  const theme = useTheme();
  const keys = Object.keys(iv.reps);
  const pOf = (k: string) => s.participants.find((p) => p.key === k);
  // peak effort per rep across the session
  const x = useMemo(() => iv.slots.map((_s, i) => i + 1), [iv]);
  const eff = useMemo(() => keys.map((k) => ({ key: k, colour: personColour(k), points: true, values: iv.slots.map((sl) => iv.reps[k][sl.slot]?.effort_pct ?? null) })), [iv, theme]);
  // HR trace in the selected rep (own start; 15 s before to 45 s after)
  const slot = cu ? iv.slots.find((x2) => x2.label === cu.slot) : null;
  const len = slot ? Math.max(...keys.map((k) => iv.reps[k][slot.slot]?.own_time_s ?? 0)) : 0;
  const xs = useMemo(() => Array.from({ length: Math.round(len + 60) }, (_v, i) => i - 15), [len]);
  const traces = useMemo(() => !replay || !slot ? [] : keys.map((k) => {
    const c = iv.reps[k][slot.slot], p = replay.people[k];
    return { key: k, colour: personColour(k), values: xs.map((xx) => (c && p ? hrPct(pOf(k), valAt(p.t, p.hr, c.clock_start_s + xx)) : null)) };
  }), [replay, slot, xs, theme]);
  const mode = effortLabel(iv.leaderboard[0]?.effort_mode);
  return (
    <section>
      <div class="head"><h2>Heart rate</h2><span class="sub">% of {mode}{s.participants.some((p) => p.hr_norm?.warn) ? " (max HR from the watch's zone settings)" : ""}</span></div>
      <div class="tg-grid2">
        <div class="card">
          <div class="head sm"><h2>{cu ? rl(cu.slot, false) : "Rep"}</h2><span class="sub">HR from 15 s before the own start (0) to 45 s after the rep</span></div>
          {traces.length ? <TLine x={xs} series={traces} height={170} xFmt={(v) => `${v} s`} yFmt={(v) => `${Math.round(v)} %`} bands={[{ a: 0, b: len }]} /> : <div class="empty">Loading…</div>}
        </div>
        <div class="card">
          <div class="head sm"><h2>Peak effort per rep</h2><span class="sub">peak HR in each rep</span></div>
          <TLine x={x} series={eff} xLabels={iv.slots.map((sl) => rl(sl.label))} height={170} yFmt={(v) => `${Math.round(v)} %`} />
        </div>
      </div>
    </section>
  );
}

/** "More detail": folded by default, its content rendered only when open (?more=1; the replay opens it too) */
function More({ open, children }: { open: boolean; children: ComponentChildren }) {
  return (
    <details class="cs-more" open={open}
      onToggle={(e) => { const o = (e.currentTarget as HTMLDetailsElement).open; if (o !== open) setQuery(o ? { more: 1 } : { more: null, replay: null, rrep: null }); }}>
      <summary>More detail</summary>
      {open && <div class="cs-more-body">{children}</div>}
    </details>
  );
}

interface ViewProps { s: TSession; replay: Replay | null; between?: ComponentChildren; more: boolean; extra?: ComponentChildren; photos?: ComponentChildren }

function IntervalsView({ s, iv, replay, between, more, extra, photos }: ViewProps & { iv: Intervals }) {
  const { query } = useLocation();
  const align = query.get("align") === "clock" ? "clock" : "own";
  const avail = iv.rep_closeups.filter((c) => c.available);
  const closest = closestRep(iv)?.label;
  const want = query.get("rep") ?? (closest && avail.some((c) => c.slot === closest) ? closest : avail[0]?.slot);
  const cu = avail.find((c) => c.slot === want) ?? avail[0] ?? null;
  const onSel = (l: string) => setQuery({ rep: l });
  return (
    <>
      <RepResults s={s} iv={iv} sel={cu?.slot ?? null} onSel={onSel} />
      <CommunitySessionSplits s={s} />
      {between}
      <More open={more}>
        <div class="tg-body">
          <div class="tg-col">
            <Leaderboard s={s} iv={iv} />
            <RepTrend s={s} iv={iv} sel={cu?.slot ?? null} />
          </div>
          <div class="tg-col side">
            {cu ? <RepCloseup s={s} iv={iv} cu={cu} replay={replay} align={align} onSel={onSel} />
              : <div class="card"><div class="empty">No rep close-up: no rep with a set distance that two or more people ran.</div></div>}
          </div>
        </div>
        {extra}
        <Breaks iv={iv} />
        <HeartIntervals s={s} iv={iv} cu={cu} replay={replay} />
        {photos}
      </More>
    </>
  );
}

// ------------------------------------------------------------------ continuous
function ContinuousView({ s, replay, between, more, extra, photos }: ViewProps) {
  const theme = useTheme();
  const c = s.continuous!;
  const race = s.group.label === "same_race";
  const sport = s.group.sport;
  const keys = s.participants.filter((p) => p.has_gps).map((p) => p.key);
  const god = c.gap_over_distance;
  const gx = useMemo(() => (god ? god.x_m.map((m) => m / 1000) : []), [god]);
  // behind the front, plotted downwards (front on top); side by side: a 250 m rolling median so 1-2 s jitter
  // (recording interval, GPS) doesn't dominate
  const smooth = !race;
  const gv = useMemo(() => (god ? Object.fromEntries(Object.entries(god.people).map(([k, v]) => [k, smooth ? rollingMedian(v, 5) : v])) : {}), [god]);
  const gs = useMemo<TSeries[]>(() => Object.entries(gv).map(([k, v]) => ({ key: k, colour: personColour(k), values: v.map(neg) })), [gv, theme]);
  // side by side: at least a 15 s scale, so a 1-2 s wobble reads as "level" rather than filling the chart
  const gRange = useMemo<[number, number] | undefined>(() => {
    if (race) return undefined;
    let lo = 0;
    for (const v of Object.values(gv)) for (const x of v) if (x !== null) lo = Math.max(lo, x);
    return lo < 15 ? [-15, 1.2] : undefined;
  }, [gv]);
  const ap = c.apart?.[0];
  // "together" stats only when they actually were (a race run apart would just show "9 % together")
  const wasTogether = !!ap && (!race || (ap.with_pct ?? 0) >= 20);
  const ax = useMemo(() => (ap ? ap.t_s.map((t) => t / 60) : []), [ap]);
  const as = useMemo<TSeries[]>(() => (ap ? [{ key: "apart", colour: cssVar("--text"), width: 1.5, values: ap.apart_m.map((v, i) => (ap.with[i] ? v : null)) },
    { key: "apart2", colour: "#8a9199", width: 1, dash: [4, 3], values: ap.apart_m.map((v, i) => (!ap.with[i] ? v : null)) }] : []), [ap, theme]);
  const [cur, setCur] = useState<{ kind: "dist" | "time"; i: number } | null>(null);
  const w = c.window_s ?? [0, 0];
  const ref = c.reference;

  // clock time per person at the cursor
  const clocks: Record<string, number> = {};
  if (cur && replay) {
    if (cur.kind === "time" && ap) for (const k of keys) clocks[k] = ap.t_s[cur.i];
    if (cur.kind === "dist" && god && ref && replay.people[ref]) {
      const rp = replay.people[ref];
      const target = god.x0_route_m + god.x_m[cur.i];
      let tr: number | null = null;
      for (let j = 1; j < rp.t.length; j++) {
        const a = rp.dist_m[j - 1], b = rp.dist_m[j];
        if (a !== null && b !== null && b >= target && rp.t[j] >= w[0] - 60) { tr = rp.t[j - 1] + ((target - a) / ((b - a) || 1)) * (rp.t[j] - rp.t[j - 1]); break; }
      }
      // everyone at the moment the front reached this point (so the map shows how far back the others were)
      const front = Math.min(...keys.map((k) => god.people[k]?.[cur.i] ?? Infinity));
      if (tr !== null) for (const k of keys) clocks[k] = tr - ((god.people[ref]?.[cur.i] ?? 0) - (isFinite(front) ? front : 0));
    }
  }
  const lines = useMemo<MapLine[]>(() => replay ? keys.flatMap((k) => [
    { key: k + ":all", colour: mapColour(k), faint: true, coords: pathBetween(replay.people[k], -1e9, 1e9) },
    { key: k, colour: mapColour(k), width: 2.5, opacity: 0.95, coords: pathBetween(replay.people[k], w[0], w[1]) },
  ]) : [], [replay]);
  const dots: MapDot[] = replay ? keys.map((k) => ({ key: k, colour: mapColour(k), initials: personInfo(k).initials,
    pos: clocks[k] !== undefined ? posAt(replay.people[k], clocks[k]) : null, title: personName(k), strong: k === s.viewer })) : [];
  const led = c.led ?? [];
  const L = god ? god.x_m[god.x_m.length - 1] || 1 : 1;
  const ledTotals: Record<string, number> = {};
  for (const l of led) ledTotals[l.person ?? "_"] = (ledTotals[l.person ?? "_"] ?? 0) + (l.to_m - l.from_m);
  const pOf = (k: string) => s.participants.find((p) => p.key === k);
  const hx = useMemo(() => { const out: number[] = []; for (let t = w[0]; t <= w[1]; t += 10) out.push(t / 60); return out; }, [w[0], w[1]]);
  const hs = useMemo<TSeries[]>(() => !replay ? [] : keys.map((k) => ({ key: k, colour: personColour(k),
    values: hx.map((m) => hrPct(pOf(k), valAt(replay.people[k].t, replay.people[k].hr, m * 60))) })), [replay, hx, theme]);
  const last = c.finish?.[c.finish.length - 1];

  if (!c.available) return <><section><p class="note">{c.why ?? "No comparison available."}</p></section>{between}<More open={more}>{extra}{photos}</More></>;
  const apartCard = ap && wasTogether && ax.length > 1 && (
    <div class="card">
      <div class="head sm"><h2>Distance apart</h2><span class="sub">straight line between you, metres · dashed: not side by side</span></div>
      <TLine x={ax} series={as} height={160} from0 xFmt={(v) => `${Math.round(v)} min`} yFmt={(v) => `${Math.round(v)} m`}
        onCursor={(i) => setCur(i === null ? null : { kind: "time", i })} />
    </div>
  );
  return (
    <>
      {c.race && c.race.length > 0 && (
        <div class="card">
          <div class="head sm"><h2>Race result</h2><span class="sub">each person's own recorded race</span></div>
          <div class="tg-mxwrap"><table class="tg-tbl lb">
            <thead><tr><th></th><th>Person</th><th class="num">Time</th><th class="num">Gap</th><th class="num">Distance</th><th class="num">{speedLabel(sport)}</th></tr></thead>
            <tbody>{c.race.map((r) => (
              <tr key={r.person}><td class="rank">{r.rank}</td>
                <td><span class={r.person === s.viewer ? "me" : ""}>{personName(r.person)}</span></td>
                <td class="num"><b>{dur(r.time_s)}</b></td><td class="num">{r.rank === 1 ? <span class="muted">first</span> : behind(r.gap_s)}</td>
                <td class="num">{r.dist_km ? `${r.dist_km.toFixed(2)} km` : "—"}</td><td class="num">{r.dist_km ? speedText(sport, (r.dist_km * 1000) / r.time_s) : "—"}</td></tr>
            ))}</tbody>
          </table></div>
          {race && c.split_at_m !== null && c.split_at_m !== undefined && last && <p class="note small">{personName(last.person)} lost contact at {(c.split_at_m / 1000).toFixed(1)} km (more than 10 s behind from there on).</p>}
        </div>
      )}
      {(c.km_splits?.length ?? 0) > 0 && (
        <div class="card">
          <div class="head sm"><h2>Splits per km</h2><span class="sub">{race ? "each person's own distance, the whole race" : c.clock === "moving" ? "moving time on the shared route" : "on the shared route"} · bold = fastest that km</span></div>
          <div class="tg-mxwrap">
            <table class="tg-tbl mx km">
              <thead><tr><th>km</th>{keys.map((k) => <th key={k} class="pcol">{personName(k)}</th>)}</tr></thead>
              <tbody>{c.km_splits!.map((r) => (
                <tr key={r.km}><td class="rl">{r.km}</td>
                  {keys.map((k) => {
                    const v = r.people[k];
                    return <td key={k} class="cell">{v?.time_s ? <><span class="v">{v.rank === 1 && keys.length > 1 ? <b>{dur(v.time_s)}</b> : dur(v.time_s)}</span>{v.gap_s ? <span class="g">{behind(v.gap_s, "s", 0)}</span> : null}</> : "—"}</td>;
                  })}</tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
      {between}
      <More open={more}>
        {wasTogether && ap && (
          <p class="tg-stats-line">{ap.with_pct ?? "—"} % of the time side by side · {ap.within_20m_pct ?? "—"} % within 20 m · median {ap.median_apart_m ?? "—"} m apart{c.common_m ? ` · ${km(c.common_m / 1000)} km same stretch` : ""}</p>
        )}
        {race && ap && !wasTogether && <p class="note small">Side by side only {ap.with_pct ?? 0} % of the time, so there is no distance-apart chart.</p>}
        <div class="tg-body">
          <div class="tg-col">
          {god && gx.length > 1 && (
            <div class="card">
              <div class="head sm"><h2>Gap over distance</h2><span class="sub">{race ? "time behind the front at each point of the course (front on top)"
                : `moving time behind the front at each point (stops made together left out; smoothed over 250 m)`}</span></div>
              <div class="tg-legend">
                {cur?.kind === "dist" ? <>
                  <span class="muted">at {gx[cur.i]?.toFixed(2)} km</span>
                  {keys.map((k) => { const v = gv[k]?.[cur.i]; return <span key={k}><i class="sw" style={{ background: personColour(k) }} />{personName(k)} <b>{v === null || v === undefined ? "—" : v < 0.5 ? "front" : behind(v, "s", 0)}</b></span>; })}
                </> : <span class="muted">hover: the map shows everyone when the front passed that point</span>}
              </div>
              <TLine x={gx} series={gs} height={180} zero from0 xIncrs={[0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50]} xFmt={(v) => `${+v.toFixed(1)} km`} yFmt={behindTick} ySize={82}
                yRange={gRange} onCursor={(i) => setCur(i === null ? null : { kind: "dist", i })} />
              {led.length > 0 && (
                <div class="tg-led" title="Who was in front, per 100 m (grey: side by side, within 2 s)">
                  <div class="rib">{led.map((l, i) => <i key={i} style={{ width: `${((l.to_m - l.from_m) / L) * 100}%`, background: l.person ? personColour(l.person) : "var(--chart-muted)" }} title={`${(l.from_m / 1000).toFixed(1)}–${(l.to_m / 1000).toFixed(1)} km: ${l.person ? personName(l.person) + " in front" : "side by side"}`} />)}</div>
                  <div class="tg-legend">
                    <span class="muted">in front</span>
                    {Object.entries(ledTotals).sort((a, b) => b[1] - a[1]).map(([k, m]) => (
                      <span key={k}><i class="sw" style={{ background: k === "_" ? "var(--chart-muted)" : personColour(k) }} />{k === "_" ? "side by side" : personName(k)} <b>{km(m / 1000)} km</b></span>
                    ))}
                  </div>
                </div>
              )}
              {(god.flagged ?? 0) > 0 && <p class="note small">{god.flagged} point{god.flagged === 1 ? "" : "s"} left out: a track snapped to another part of the path (GPS / projection), not a real gap.</p>}
            </div>
          )}
            {apartCard}
          </div>
          <div class="tg-col side">
            <div class="card">
              <div class="head sm"><h2>Map</h2><span class="sub">{cur ? "dots at the chart cursor" : "hover a chart to place the dots"}</span></div>
              <div class="tg-cu-map tall">
                {replay ? <TogetherMap lines={lines} dots={dots} fitKey={s.gid} /> : <div class="map bordered"><div class="msg">Loading track…</div></div>}
              </div>
              <MapLegend keys={keys} viewer={s.viewer} />
            </div>
          </div>
        </div>
        {extra}
        {hs.length > 0 && (
          <section>
            <div class="head"><h2>Heart rate</h2><span class="sub">% of max HR over the clock{s.participants.some((p) => p.hr_norm?.warn) ? " (max HR from the watch's zone settings)" : ""}</span></div>
            <TLine x={hx} series={hs} height={180} xFmt={(v) => `${Math.round(v)} min`} yFmt={(v) => `${Math.round(v)} %`} />
          </section>
        )}
        {photos}
      </More>
    </>
  );
}


/** who's which line on the map (the lower tracks are drawn wider, so overlapping ones stay visible) */
function MapLegend({ keys, viewer }: { keys: string[]; viewer: string | null }) {
  return (
    <div class="tg-legend" title="Where you were side by side the tracks overlap: the one underneath shows as a wider outline">
      {keys.map((k) => <span key={k}><i class="sw" style={{ background: mapColour(k) }} />{personName(k)}{k === viewer ? " (you)" : ""}</span>)}
      {keys.length > 1 && <span class="muted">overlapping: {personName(keys[keys.length - 1])} on top, {keys.slice(0, -1).map(personName).join(", ")} as the outline</span>}
    </div>
  );
}

// ------------------------------------------------------------------ page
/** participants (and the per-person maps the tables iterate) in one fixed order: the viewer first, then people order */
function ordered(s: TSession): TSession {
  const order = [...(s.viewer ? [s.viewer] : []), ...allPeople()];
  const rank = (k: string) => { const i = order.indexOf(k.split("#")[0]); return (i < 0 ? 99 : i) + (k.includes("#") ? 0.5 : 0); };
  const sortObj = <T,>(o: Record<string, T>) => Object.fromEntries(Object.entries(o).sort((a, b) => rank(a[0]) - rank(b[0])));
  const iv = s.intervals;
  return {
    ...s,
    participants: [...s.participants].sort((a, b) => rank(a.key) - rank(b.key)),
    intervals: iv ? { ...iv, reps: sortObj(iv.reps), breaks: sortObj(iv.breaks), cumulative: { ...iv.cumulative, people: sortObj(iv.cumulative.people) },
      rep_closeups: iv.rep_closeups.map((c) => (c.available ? { ...c, own: c.own && { ...c.own, people: sortObj(c.own.people) }, clock: c.clock && { ...c.clock, people: sortObj(c.clock.people) } } : c)) } : null,
  };
}

function Unverified({ s, replay }: { s: TSession; replay: Replay | null }) {
  const missing = s.participants.filter((p) => p.role !== "other" && (!p.has_fit || !p.has_gps));
  const withGps = s.participants.filter((p) => p.has_gps);
  const pr = s.group.pairs?.[0];
  const ov = pr?.why?.match(/overlap (\d+) %/)?.[1];
  const match = [pr?.start_dist_m !== null && pr?.start_dist_m !== undefined ? `started ${Math.round(pr.start_dist_m)} m` : null,
    pr?.dstart_s !== null && pr?.dstart_s !== undefined ? `${secs(Math.abs(pr.dstart_s))} apart` : null].filter(Boolean).join(" and ");
  const lines = useMemo<MapLine[]>(() => replay ? withGps.map((p) => ({ key: p.key, colour: mapColour(p.key), width: 3, coords: pathBetween(replay.people[p.key], -1e9, 1e9) })) : [], [replay]);
  return (
    <section>
      <div class={"tg-unv" + (withGps.length ? " withmap" : "")}>
        <div>
          <h2>Summary only</h2>
          <p>{missing.length ? `${missing.map((p) => personName(p.key)).join(" and ")} ${missing.length === 1 ? "has" : "have"} no ${missing.some((p) => !p.has_fit) ? "watch file" : "GPS track"} for this activity` : "There is no track to compare"}, so
            there are no rep times, gaps or replay. It's listed because you {match ? `${match}` : "started at the same place and time"}{ov ? `, and the two activities overlap ${ov} % in time` : ""}.</p>
          <div class="tg-mxwrap"><table class="tg-tbl lb">
            <thead><tr><th>Person</th><th>Activity</th><th class="num">Distance</th><th class="num">Time</th><th>Track</th></tr></thead>
            <tbody>{s.participants.map((p) => (
              <tr key={p.key}><td>{personName(p.key)}</td>
                <td><a href={withPerson(`/activities/${p.activity_id}`, p.person)}>{p.name}</a></td>
                <td class="num">{p.dist_km ? `${km(p.dist_km)} km` : "—"}</td><td class="num">{dur(p.dur_s)}</td>
                <td>{p.has_gps ? "GPS" : p.has_fit ? "no GPS" : <span class="muted">no watch file</span>}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
        {withGps.length > 0 && (
          <div class="tg-unv-map">
            <div class="tg-cu-map">{replay ? <TogetherMap lines={lines} dots={[]} fitKey={s.gid + ":unv"} /> : <div class="map bordered"><div class="msg">Loading track…</div></div>}</div>
            <MapLegend keys={withGps.map((p) => p.key)} viewer={s.viewer} />
          </div>
        )}
      </div>
    </section>
  );
}

/** the replay (inside "More detail"): the panel when started, else one button */
function ReplaySection({ s, rp, replay, open }: { s: TSession; rp: { data: Replay | null; error: string | null }; replay: Replay | null; open: boolean }) {
  const { query } = useLocation();
  return (
    <section class="tg-replay-sec">
      <div class="head"><h2>Replay</h2><span class="sub">everyone on one clock{open ? " · space = play / pause" : ""}</span></div>
      {!open ? <button onClick={() => setQuery({ replay: 1, rrep: null })}>Start replay</button>
        : replay ? <ReplayPanel s={s} r={replay} hr startRep={query.get("rrep")} onClose={() => setQuery({ replay: null, rrep: null })} />
        : <div class="empty">{rp.error ?? (rp.data && !rp.data.available ? rp.data.note ?? "No positions to replay." : "Loading positions…")}</div>}
    </section>
  );
}

export function TogetherSession({ gid }: { gid: string }) {
  const { query } = useLocation();
  const { data: raw, error } = useApi<TSession>(`/api/together/${encodeURIComponent(gid)}`);
  const s = useMemo(() => (raw ? ordered(raw) : null), [raw]);
  const { data: c } = useCommunity(gid);
  const replayOpen = query.get("replay") === "1";
  const more = query.get("more") === "1" || replayOpen;
  if (s) setPeopleInfo(s.people);
  const facts = useMemo(() => (s ? sessionFacts(s, c, personName) : null), [s, c]);
  const gpsN = s ? s.participants.filter((p) => p.has_gps).length : 0;
  const canMap = !!s && gpsN >= 1 && (s.group.no_track || (more && !!(s.intervals?.rep_closeups.some((x) => x.available) || s.continuous?.available)));
  // replay positions: the maps' dots, the replay and the share card sketch; loaded once a map is shown
  const rp = useApi<Replay>(s && (canMap || replayOpen) ? `/api/together/${encodeURIComponent(gid)}/replay` : null);
  useEffect(() => {
    const t = c?.meta.title || c?.display_title || s?.group.workout;
    document.title = s ? `${t} · ${shortDate(s.group.date, "0000")} · Sessions` : "Sessions";
  }, [s, c]);
  useEffect(() => { if (replayOpen) document.querySelector(".tg-replay-sec")?.scrollIntoView({ block: "nearest" }); }, [replayOpen, !!rp.data, query.get("rrep")]);

  if (error) return (
    <main class="tg">
      <a href="/community">‹ Sessions</a>
      <h1>Session not found</h1>
      <div class="note">{/no together group|not found|404/i.test(error) ? `There is no club session "${gid}". It may have been renamed or merged when the sessions were re-detected.` : error}</div>
      <p><a href="/community">See all sessions</a></p>
    </main>
  );
  if (!s || !facts) return <main class="tg"><div class="empty">Loading…</div></main>;
  const replay = rp.data?.available ? rp.data : null;
  const unverified = s.group.no_track || gpsN < 2 && !s.intervals;
  const replayable = !unverified && (gpsN >= 2 || !!s.intervals);
  const between = <SamePlace gid={s.gid} />;
  const extra = replayable ? <ReplaySection s={s} rp={rp} replay={replay} open={replayOpen} /> : null;
  const photos = <CommunitySessionPhotos gid={s.gid} />;
  return (
    <main class="tg cs-page">
      <CommunitySessionHero gid={s.gid} s={s} replay={replay} facts={facts} links={<CompareLink s={s} />} />
      {s.group.label === "same_place_diff_workout" && <p class="note small">Near-miss: same place and time, but different workouts (e.g. only a shared warm-up); hidden from the list by default.</p>}
      <Caveats s={s} />
      <CommunitySessionMembers gid={s.gid} s={s} />
      {unverified ? <><Unverified s={s} replay={replay} />{between}<More open={more}>{photos}</More></>
        : s.intervals ? <IntervalsView s={s} iv={s.intervals} replay={replay} between={between} more={more} extra={extra} photos={photos} />
        : s.continuous ? <ContinuousView s={s} replay={replay} between={between} more={more} extra={extra} photos={photos} />
        : <><section><p class="note">No comparison for this session.</p></section>{between}<More open={more}>{extra}{photos}</More></>}
    </main>
  );
}
