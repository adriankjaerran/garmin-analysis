// Motivation pieces of the Community pages (docs/community.md "Motivation"): the club goal (editable, PATCH
// /api/community/goals/{id}), "faster together than alone?" per member, badges (earned + locked with progress),
// the newest badges strip, and the rivalry timeline per pair (cumulative rep wins + the gap per session).
import { useEffect, useRef, useState } from "preact/hooks";
import { apiUrl } from "../lib/api";
import { isoLocal, shortDate } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { delta, deltaCls, monthLabel, paceKm, plural, type Badge, type Company, type CompanyShort, type Goal, type Goals, type Rivalry } from "../lib/community";
import { Av } from "./CommunityBits";
import "../pages/club-motivation.css";

type N = number | null;
const fmtV = (v: number, unit: string) => (unit === "sessions" || unit === "reps" || unit === "m" || v >= 100 ? Math.round(v).toLocaleString("en-GB") : v.toFixed(1));
const STATUS: Record<Goal["status"], string> = { done: "Done 🎉", ahead: "Ahead of pace", on_track: "On track", behind: "Behind pace", ended: "Ended", upcoming: "Not started" };

// ------------------------------------------------------------------ goal
async function send(method: string, url: string, body?: unknown): Promise<Goals> {
  const r = await fetch(apiUrl(url), { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `${r.status} ${r.statusText}`);
  return j as Goals;
}

/** progress bar with the "where we should be by now" tick */
export function GoalBar({ g, big }: { g: Goal; big?: boolean }) {
  return (
    <div class={"cg-bar" + (big ? " big" : "") + " " + g.status} role="progressbar" aria-valuenow={g.value} aria-valuemax={g.target} aria-label={g.title}>
      <i style={{ width: `${Math.max(1.5, g.pct * 100)}%` }} />
      {g.status !== "done" && g.status !== "upcoming" && g.expected_pct > 0 && g.expected_pct < 1 &&
        <b style={{ left: `${g.expected_pct * 100}%` }} title={`by today at an even pace: ${fmtV(g.expected, g.unit)} ${g.unit}`} />}
    </div>
  );
}

/** the club goal: title, value of target, bar, status, members' shares; Edit opens an inline form (fetches its own,
 *  un-memoised data so an edit shows everywhere at once) */
export function GoalCard({ initial, compact }: { initial?: Goal | null; compact?: boolean }) {
  const [data, setData] = useState<Goals | null>(null);
  const [edit, setEdit] = useState<Partial<Goal> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { let live = true; fetch(apiUrl("/api/community/goals")).then((r) => r.json()).then((j) => live && setData(j)).catch(() => {}); return () => { live = false; }; }, []);
  const g = data?.goal ?? initial ?? null;
  if (!g) return null;
  const others = (data?.goals ?? []).filter((x) => x.id !== g.id);
  const save = async () => {
    if (!edit) return;
    setBusy(true); setErr(null);
    try {
      const body = { title: edit.title, metric: edit.metric, target: Number(edit.target), from: edit.from, to: edit.to };
      setData(edit.id === "new" ? await send("POST", "/api/community/goals", body) : await send("PATCH", `/api/community/goals/${encodeURIComponent(edit.id!)}`, body));
      setEdit(null);
    } catch (e) { setErr(String((e as Error).message || e)); } finally { setBusy(false); }
  };
  const del = async () => {
    if (!edit?.id || edit.id === "new" || edit.id === "default" || !confirm(`Delete the goal “${edit.title}”?`)) return;
    try { setData(await send("DELETE", `/api/community/goals/${encodeURIComponent(edit.id)}`)); setEdit(null); } catch (e) { setErr(String((e as Error).message || e)); }
  };
  const metrics = data?.metrics ?? [{ id: "sessions", unit: "sessions", label: "sessions" }, { id: "km", unit: "km", label: "km together" }];
  const y = isoLocal().slice(0, 4);
  return (
    <section class={"cm-panel cg" + (compact ? " compact" : "")}>
      <div class="cg-top">
        <span class="cg-flag" aria-hidden="true">🎯</span>
        <div class="cg-t">
          <span class="lbl">Club goal{g.default ? " · suggested" : ""}</span>
          <b>{g.title}</b>
          <span class="muted small">{shortDate(g.from, "x")} – {shortDate(g.to, "x")}{g.status !== "ended" && g.status !== "upcoming" ? ` · ${plural(g.days_left, "day")} left` : ""}</span>
        </div>
        <div class="cg-v"><b>{fmtV(g.value, g.unit)}</b><span> / {fmtV(g.target, g.unit)} {g.unit}</span></div>
        <span class={"cg-status " + g.status}>{STATUS[g.status]}</span>
        <button class="btn sm" onClick={() => setEdit(edit ? null : { ...g })}>{edit ? "Close" : "Edit"}</button>
      </div>
      <GoalBar g={g} big />
      <div class="cg-foot">
        <span>{g.status === "done" ? `Reached in ${plural(g.n_sessions, "session")}` : g.status === "ended" ? `${Math.round(g.pct * 100)} % reached` :
          g.status === "upcoming" ? `Starts ${shortDate(g.from, "x")}` :
          <>{fmtV(g.remaining, g.unit)} {g.unit} to go{g.days_left > 0 && g.unit === "sessions" ? ` · about one every ${Math.max(1, Math.round(g.days_left / Math.max(1, g.remaining)))} days` : ""}</>}</span>
        {g.unit !== "sessions" && g.by_member.length > 0 && <span class="cg-members">{g.by_member.map((m) => <span><Av p={m.person} size="xs" />{fmtV(m.value, g.unit)}</span>)}</span>}
        {g.unit === "sessions" && g.by_member.length > 0 && <span class="cg-members">{g.by_member.map((m) => <span title={`${personName(m.person)} was at ${m.value} of them`}><Av p={m.person} size="xs" />{m.value}</span>)}</span>}
      </div>
      {!compact && others.length > 0 && !edit && (
        <div class="cg-others">
          {others.map((o) => (
            <div class="cg-o">
              <span class="t">{o.title}</span><GoalBar g={o} /><span class="muted small">{o.text}</span>
              <button class="btn sm ghost" onClick={() => setEdit({ ...o })}>Edit</button>
            </div>
          ))}
        </div>
      )}
      {edit && (
        <form class="cg-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <label>Title<input value={edit.title ?? ""} placeholder="e.g. 500 km together in 2026" onInput={(e) => setEdit({ ...edit, title: (e.target as HTMLInputElement).value })} /></label>
          <label>Target<input type="number" min="1" step="any" value={edit.target ?? ""} onInput={(e) => setEdit({ ...edit, target: Number((e.target as HTMLInputElement).value) })} /></label>
          <label>Of<select value={edit.metric} onChange={(e) => setEdit({ ...edit, metric: (e.target as HTMLSelectElement).value as Goal["metric"] })}>
            {metrics.map((m) => <option value={m.id}>{m.label}</option>)}</select></label>
          <label>From<input type="date" value={edit.from} onInput={(e) => setEdit({ ...edit, from: (e.target as HTMLInputElement).value })} /></label>
          <label>To<input type="date" value={edit.to} onInput={(e) => setEdit({ ...edit, to: (e.target as HTMLInputElement).value })} /></label>
          <div class="acts">
            <button class="btn primary sm" type="submit" disabled={busy}>{edit.id === "new" ? "Add goal" : "Save"}</button>
            {edit.id !== "new" && <button class="btn sm ghost" type="button" onClick={() => setEdit({ id: "new", title: `500 km together in ${y}`, metric: "km", target: 500, from: `${y}-01-01`, to: `${y}-12-31` })}>+ Another goal</button>}
            {edit.id !== "new" && edit.id !== "default" && <button class="btn sm ghost danger" type="button" onClick={del}>Delete</button>}
            {err && <span class="err small">{err}</span>}
          </div>
        </form>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ faster together than alone?
function DotStrip({ c }: { c: Company }) {
  useTheme();
  const vals = [...c.joint.map((j) => j.delta), ...c.solo.map((s) => s.delta)];
  const lim = Math.max(4, Math.ceil(Math.max(...vals.map(Math.abs), 0)));
  const X = (v: number) => 56 + (v / lim) * 41;
  const row = (label: string, pts: { v: number; href?: string; tip: string }[], avg: N, col: string, y: number) => (
    <g>
      <text x="0.5%" y={y + 4} class="lbl">{label}</text>
      {pts.map((p) => {
        const dot = <circle cx={`${X(p.v)}%`} cy={y} r="5" fill={col} fill-opacity="0.75" stroke="var(--surface)" stroke-width="1"><title>{p.tip}</title></circle>;
        return p.href ? <a href={p.href}>{dot}</a> : dot;
      })}
      {avg !== null && <line x1={`${X(avg)}%`} x2={`${X(avg)}%`} y1={y - 11} y2={y + 11} stroke="var(--text)" stroke-width="3" stroke-linecap="round"><title>average {delta(avg)}</title></line>}
    </g>
  );
  return (
    <svg class="cc-strip" width="100%" height="64" role="img" aria-label="form deltas together vs alone">
      <line x1="56%" x2="56%" y1="4" y2="54" stroke="var(--hairline)" stroke-dasharray="3 3" />
      {row("Together", c.joint.map((j) => ({ v: j.delta, href: `/community/${j.gid}`, tip: `${j.date} · ${delta(j.delta)} vs form (club session)` })), c.joint_avg, personColour(c.person), 18)}
      {row("Alone", c.solo.map((s) => ({ v: s.delta, href: `/activities/${s.activity_id}`, tip: `${s.date} · ${delta(s.delta)} vs form${s.name ? ` · ${s.name}` : ""}` })), c.solo_avg, "var(--chart-muted)", 46)}
      <text x={`${X(-lim)}%`} y="63" class="ax" text-anchor="middle">{delta(-lim, 0)}</text>
      <text x="56%" y="63" class="ax" text-anchor="middle">±0</text>
      <text x={`${X(lim)}%`} y="63" class="ax" text-anchor="middle">{delta(lim, 0)} vs form</text>
    </svg>
  );
}

/** one member: the verdict as a big number, averages and counts, pace per rep family; `full` adds the dot strip */
export function CompanyCard({ p, c, full }: { p: string; c: CompanyShort | Company; full?: boolean }) {
  const v = c.diff;
  return (
    <div class={"cc " + (c.verdict ?? "none")} style={{ "--p": personColour(p) } as any}>
      <div class="cc-top">
        {!full && <Av p={p} size="lg" />}
        <b class={"cc-v " + deltaCls(v)}>{v === null ? "—" : delta(v)}</b>
        <div class="cc-t">
          <span class="h">{c.text ?? `Not enough data for ${personName(p)} yet`}</span>
          <span class="muted small">
            {v === null ? "needs a scored club session and a solo session of the same kind within 60 days"
              : <>together {delta(c.joint_avg)} ({c.n_joint}) · alone {delta(c.solo_avg)} ({c.n_solo}) · VDOT vs form</>}
            {c.thin && v !== null && <span class="cc-thin" title="few sessions to compare: read it as a hint, not a verdict"> · thin data</span>}
          </span>
        </div>
      </div>
      {full && "joint" in c && (c.joint.length > 0 || c.solo.length > 0) && <DotStrip c={c as Company} />}
      {c.pace.length > 0 && (
        <div class="cc-pace">
          {c.pace.map((x) => (
            <div class="r" title={`${x.n_joint} club / ${x.n_solo} solo interval sessions`}>
              <span class="l">{x.label}</span>
              <span class="num"><b>{paceKm(x.joint_s_km)}</b><span class="muted">/km together</span></span>
              <span class="num"><b>{paceKm(x.solo_s_km)}</b><span class="muted">/km alone</span></span>
              <span class={"d " + (x.diff_s_km >= 1 ? "up" : x.diff_s_km <= -1 ? "down" : "")}>{x.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ badges
export function BadgeTile({ b, person }: { b: Badge; person?: string }) {
  const body = <>
    <span class="e" aria-hidden="true">{b.emoji}</span>
    <b class="n">{b.name}</b>
    <span class="d">{b.description}</span>
    {b.earned
      ? <span class="w">{person && <Av p={person} size="xs" />}{shortDate(b.date!, isoLocal())}{b.detail && <span class="x">{b.detail}</span>}</span>
      : <span class="p"><span class="cb-bar"><i style={{ width: `${Math.round(b.progress * 100)}%` }} /></span>
          <span class="muted">{b.detail?.startsWith("held by") ? b.detail : `${fmtV(b.value ?? 0, b.unit)} / ${fmtV(b.target, b.unit)}${b.unit && b.unit !== "VDOT" ? ` ${b.unit}` : b.unit === "VDOT" ? " vs form" : ""}`}</span></span>}
  </>;
  const cls = "cb" + (b.earned ? " on" : " off");
  const tip = `${b.name}: ${b.description}${b.earned ? ` — earned ${b.date}` : ` — ${Math.round(b.progress * 100)} %`}`;
  return b.earned && b.gid ? <a class={cls} href={`/community/${b.gid}`} title={tip}>{body}</a> : <div class={cls} title={tip}>{body}</div>;
}

export function BadgeGrid({ badges, earned, total }: { badges: Badge[]; earned: number; total: number }) {
  const [all, setAll] = useState(false);
  const on = badges.filter((b) => b.earned), off = badges.filter((b) => !b.earned);
  const shownOff = all ? off : off.slice(0, 6);
  return (
    <div class="cb-wrap">
      <div class="cb-meter"><span class="cb-bar wide"><i style={{ width: `${(100 * earned) / Math.max(1, total)}%` }} /></span><span><b>{earned}</b> of {total} badges</span></div>
      <div class="cb-grid">{on.map((b) => <BadgeTile key={b.id} b={b} />)}</div>
      {off.length > 0 && <>
        <div class="cb-sub">Still to earn <span class="muted">· closest first</span></div>
        <div class="cb-grid">{shownOff.map((b) => <BadgeTile key={b.id} b={b} />)}</div>
        {off.length > 6 && <button class="btn sm ghost" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${off.length} locked badges`}</button>}
      </>}
    </div>
  );
}

/** newest badges over all members, a horizontal strip */
export function LatestBadges({ badges }: { badges: Badge[] }) {
  if (!badges.length) return null;
  return (
    <div class="cb-strip">
      {badges.map((b) => (
        <a class="cb-chip" href={b.gid ? `/community/${b.gid}` : `/community/members/${b.person}`} title={`${personName(b.person!)} · ${b.name}: ${b.description}${b.detail ? ` (${b.detail})` : ""}`}>
          <span class="e" aria-hidden="true">{b.emoji}</span>
          <span class="t"><b>{b.name}</b><span class="muted"><Av p={b.person!} size="xs" /> {personName(b.person!)} · {shortDate(b.date!, isoLocal())}</span></span>
        </a>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ rivalry timeline
/** cumulative rep wins (steps, person colours) over the sessions, and below each session's gap per km (bar towards
 *  the faster one). Equal spacing per session (they can be years apart). `viewer` is shown first. */
export function RivalryCard({ r, viewer }: { r: Rivalry; viewer?: string | null }) {
  useTheme();
  const [hover, setHover] = useState<number | null>(null);
  const flip = viewer === r.b;
  const [a, b] = flip ? [r.b, r.a] : [r.a, r.b];
  const sgn = flip ? -1 : 1;
  const tl = r.timeline;
  const n = tl.length;
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => { if (el.clientWidth) setW(el.clientWidth); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = 190, L = 34, R = 14, top = 12, midH = 92, gapTop = 122, gapH = 46;
  const xs = (i: number) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const maxW = Math.max(1, ...tl.map((t) => Math.max(t.cum_wins[a], t.cum_wins[b])));
  const yW = (v: number) => top + midH - (v / maxW) * midH;
  const gaps = tl.map((t) => (t.gap_s_km ?? t.mean_gap_s) * sgn);
  const gMax = Math.max(1, ...gaps.map(Math.abs));
  const g0 = gapTop + gapH / 2;
  const path = (p: string) => `M${xs(0)} ${yW(0)} V${yW(tl[0].cum_wins[p])} ` + tl.slice(1).map((t, i) => `H${xs(i + 1)} V${yW(t.cum_wins[p])}`).join(" ");
  const ca = personColour(a), cb = personColour(b);
  const h = hover !== null ? tl[hover] : null;
  const unit = tl.some((t) => t.gap_s_km !== null) ? "s/km" : "s/rep";
  return (
    <div class="cr">
      <div class="cr-top">
        <span class="cm-pl"><Av p={a} size="lg" />{personName(a)}</span>
        <b class="cr-score">{r.wins[a]}<span class="dash">–</span>{r.wins[b]}</b>
        <span class="cm-pl rt">{personName(b)}<Av p={b} size="lg" /></span>
      </div>
      <div class="cr-sub muted small">shared reps won (and races) over {plural(r.sessions, "session")}{r.ties ? ` · ${plural(r.ties, "tie")}` : ""}</div>
      {r.trend && <div class={"cr-trend " + r.trend.kind}><span aria-hidden="true">{r.trend.kind === "closing" ? "📉" : r.trend.kind === "pulling_away" ? "📈" : r.trend.kind === "turned" ? "🔄" : "➖"}</span>{r.trend.text}</div>}
      <div ref={box}><svg class="cr-svg" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={r.headline} onMouseLeave={() => setHover(null)}>
        <text x="2" y={top + midH / 2 + 4} class="ax">wins</text>
        <line x1={L} x2={W - R} y1={yW(0)} y2={yW(0)} stroke="var(--hairline)" />
        <text x={L - 4} y={yW(maxW) + 4} class="ax" text-anchor="end">{maxW}</text>
        <path d={path(b)} fill="none" stroke={cb} stroke-width="2.5" />
        <path d={path(a)} fill="none" stroke={ca} stroke-width="2.5" />
        <text x="2" y={g0 + 4} class="ax">gap</text>
        <line x1={L} x2={W - R} y1={g0} y2={g0} stroke="var(--hairline)" stroke-dasharray="3 3" />
        {tl.map((t, i) => {
          const v = gaps[i], hgt = (Math.abs(v) / gMax) * (gapH / 2);
          const bw = Math.max(6, Math.min(26, (W - L - R) / Math.max(2, n) * 0.4));
          return <g>
            <rect x={xs(i) - bw / 2} y={v > 0 ? g0 - hgt : g0} width={bw} height={Math.max(1, hgt)} rx="2" fill={v > 0 ? ca : cb} opacity={hover === null || hover === i ? 0.9 : 0.4} />
            <circle cx={xs(i)} cy={yW(t.cum_wins[a])} r={hover === i ? 5 : 3.5} fill={ca} stroke="var(--surface)" stroke-width="1.5" />
            <circle cx={xs(i)} cy={yW(t.cum_wins[b])} r={hover === i ? 5 : 3.5} fill={cb} stroke="var(--surface)" stroke-width="1.5" />
            <rect x={xs(i) - (W - L - R) / Math.max(1, n) / 2} y="0" width={(W - L - R) / Math.max(1, n)} height={H} fill="transparent"
              onMouseEnter={() => setHover(i)} onClick={() => { location.href = `/community/${t.gid}`; }} style={{ cursor: "pointer" }} />
          </g>;
        })}
      </svg></div>
      <div class="cr-dates">{tl.map((t, i) => (i % Math.max(1, Math.ceil(52 / Math.max(1, (W - L - R) / Math.max(1, n - 1)))) === 0 || hover === i) &&
        <a href={`/community/${t.gid}`} class={hover === i ? "on" : ""} style={{ left: `${(xs(i) / W) * 100}%` }}>{monthLabel(t.date.slice(0, 7), true)}</a>)}</div>
      <div class="cr-read small">
        {h ? <>
          <b>{shortDate(h.date, isoLocal())}</b> {h.title} · {h.kind === "race" ? "race" : plural(h.n, "shared rep")}:
          <span style={{ color: ca }}> {personName(a)} {h.wins[a]}</span>–<span style={{ color: cb }}>{h.wins[b]} {personName(b)}</span>
          {" · "}gap {Math.abs(h.mean_gap_s).toFixed(1)} s{h.kind === "reps" ? "/rep" : ""}{h.gap_s_km !== null ? ` (${Math.abs(h.gap_s_km).toFixed(0)} s/km)` : ""} to {personName((h.mean_gap_s * sgn) > 0 ? a : b)}
          {h.closest_s <= 1 ? ` · closest ${h.closest_s.toFixed(1)} s` : ""}
        </> : <span class="muted">Lines = cumulative wins · bars = the gap per session ({unit}), towards the faster one · hover / click a session</span>}
      </div>
    </div>
  );
}
