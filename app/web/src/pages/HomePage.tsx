// Home: the latest active day (activities table + a one-line daily summary), current shape per discipline (one
// line, links to Fitness), a 5-week training log (click a day = Day view), volume per sport for the last 7 / 30 days
// vs typical (previous 4 weeks / previous 12 months), and the recent workouts. One request: /api/home.
import { useEffect, useMemo, useState } from "preact/hooks";
import { useApi, type Activity, type Day } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { SPORT_LABEL, addDays, daysBetween, dur, hm, km, num, secs, shortDate, sportColor } from "../lib/format";
import { mmss, type SearchRow } from "../lib/search";
import { Table, type Col } from "../components/Table";
import { PageHeader } from "../components/PageHeader";
import { DistCell, distSort, distStr, kmTotal, speedText, trustedKm, workoutText, workoutTip } from "../components/ActivityBits";
import { ProgressCard } from "../components/ProgressCard";
import { personName } from "../lib/people";
import "../fitness.css";   // --disc-* tokens
import "./home.css";

interface Agg { n: number; km: number; s: number; climb: number; n_untrusted?: number }
interface VolRow { sport: string; week: Agg; prev4: Agg; month: Agg; year30: Agg }
interface Shape { label: string; unit: string; mean: number | null; sd: number | null; confidence?: string | null; pct_of_best?: number | null;
  trend_90d: number | null; race_equivalents_s?: Record<string, number> | null; last_evidence?: { date: string; id: number; kind: string } | null;
  date: string; weekly: number[] }
type HomeRow = SearchRow & { dur_s: number | null; type_label?: string | null; fit: boolean; track: boolean; swim?: Activity["swim"] };

/** "6×1200" -> "6×1.2k", "1×1300 + 2×3000" -> "1×1.3k + 2×3k" (the training log on a phone: never clip digits) */
export const compactSig = (s: string) => s.replace(/×(\d{4,})(?!\d)/g, (_m, d) => `×${(Number(d) / 1000).toFixed(1).replace(/\.0$/, "")}k`);
/** a workout label as tokens that wrap only at "×" and before "+" (word-break: keep-all in CSS) */
function SigTokens({ s }: { s: string }) {
  const parts = s.split(" + ");
  return <>{parts.map((p, i) => <span key={i}>{i > 0 && <><wbr />+</>}{p.split("×").map((q, j) => <span key={j}>{j > 0 && <>×<wbr /></>}{q}</span>)}</span>)}</>;
}
interface Home {
  anchor: string; latest: string | null; periods: { week: [string, string]; month: [string, string]; log: [string, string] };
  latest_day: (Omit<Day, "hr"> & { summary_date: string | null; sleep_date: string | null }) | null; volume: VolRow[]; days: Record<string, string[]>; recent: HomeRow[];
  fitness: Record<string, Shape> | null;
}

// ------------------------------------------------------------------ small inline charts
/** a line of weekly values (shape) */
function LineSpark({ values, color, w = 72, h = 16 }: { values: number[]; color: string; w?: number; h?: number }) {
  const v = values.filter((x) => x !== null && isFinite(x));
  if (v.length < 2) return null;
  const lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 1 - ((x - lo) / r) * (h - 2)).toFixed(1)}`).join(" ");
  return <svg class="spark" width={w} height={h}><polyline points={pts} fill="none" stroke={color} stroke-width={1.5} /></svg>;
}

/** value vs typical: bar = value, tick = typical, both on the same scale */
function VsBar({ v, typ, color }: { v: number; typ: number; color: string }) {
  const W = 48, m = Math.max(v, typ, 1e-9) * 1.05;
  return (
    <svg class="vsbar" width={W} height={8}>
      <rect x={0} y={1} width={W} height={6} rx={2} fill="var(--surface-tint)" />
      <rect x={0} y={1} width={(v / m) * W} height={6} rx={2} fill={color} fill-opacity={0.8} />
      {typ > 0 && <line x1={(typ / m) * W} x2={(typ / m) * W} y1={0} y2={8} stroke="var(--text)" stroke-width={1.5} />}
    </svg>
  );
}

function pctDelta(v: number, typ: number) {
  if (!typ) return v ? <span class="muted">new</span> : <span class="muted">—</span>;
  const p = Math.round((v / typ - 1) * 100);
  if (!v) return <span class="muted">none</span>;
  return <span class={Math.abs(p) >= 25 ? "" : "muted"}>{p > 0 ? "+" : p < 0 ? "−" : "±"}{Math.abs(p)} %</span>;
}

// ------------------------------------------------------------------ shape line
const DISC_SHORT: Record<string, string> = { interval: "Interval", long: "Long", ski: "Ski", overall: "Overall" };
const DISC_COLOR: Record<string, string> = { interval: "var(--disc-interval)", long: "var(--disc-long)", ski: "var(--disc-ski)", overall: "var(--disc-overall)" };

/** a shape without a race / hard session for more than this many days is stale: greyed, "last evidence 12 mo ago" */
const STALE_DAYS = 90;
/** "12 d" / "5 mo" / "2.5 y" */
export const agoShort = (d: number) => d < 60 ? `${d} d` : d < 730 ? `${Math.round(d / 30.4)} mo` : `${(d / 365).toFixed(1).replace(/\.0$/, "")} y`;

function ShapeLine({ fit }: { fit: Record<string, Shape> }) {
  // one race prediction: the interval + long shapes blended by precision (fitness.json overall.race_prediction),
  // so a stale long shape (its sd grows every day without evidence) doesn't give a second, minutes-apart answer
  const pred = fit.overall?.race_equivalents_s;
  return (
    <div class="home-shape">
      <a class="lbl" href="/fitness" title="Current shape per discipline (Daniels VDOT scale) — open Fitness">Shape</a>
      {Object.entries(fit).map(([k, s]) => {
        const ov = k === "overall";
        const t = s.trend_90d;
        const eq = s.race_equivalents_s;
        const ago = !ov && s.last_evidence ? daysBetween(s.last_evidence.date, s.date) : null;
        const stale = !ov && (ago === null || ago > STALE_DAYS);
        const tip = [s.label, ov ? `${num(s.mean, 1)} % of personal best` : `${num(s.mean, 1)} ± ${num(s.sd, 1)} ${s.unit}`,
          s.pct_of_best ? `${num(s.pct_of_best, 0)} % of best` : "", t !== null ? `${t >= 0 ? "+" : ""}${num(t, 1)} in 90 days` : "",
          !ov && eq ? `≈ 5k ${mmss(eq["5k"])}, 10k ${dur(eq["10k"])}` : "",
          ov && pred ? `today ≈ 5k ${mmss(pred["5k"])}, 10k ${dur(pred["10k"])}, half ${dur(pred.half)} (interval + long shape blended by how certain each is)` : "",
          s.last_evidence ? `last hard evidence ${s.last_evidence.date}` : "",
          stale ? `no race or hard session for ${ago === null ? "ever" : `${ago} days`}: the value is carried forward and its uncertainty grows` : "",
          s.confidence ? `confidence ${s.confidence}` : ""].filter(Boolean).join(" · ");
        return (
          <a key={k} class={"disc" + (stale ? " stale" : "")} href={`/fitness?d=${k === "overall" ? "interval,long,ski" : k}`} title={tip}
            style={stale ? { opacity: 0.55 } : undefined}>
            <span class="dot" style={{ background: DISC_COLOR[k] }} />
            <span class="nm">{DISC_SHORT[k] ?? k}</span>
            <b>{ov ? `${num(s.mean, 0)} %` : num(s.mean, 1)}</b>
            {!ov && s.sd !== null && <span class="muted">±{num(s.sd, 1)}</span>}
            {!ov && !stale && s.pct_of_best && <span class="muted">{num(s.pct_of_best, 0)} %</span>}
            {stale ? <span class="muted">last evidence {ago === null ? "none" : `${agoShort(ago)} ago`}</span>
              : t !== null && <span class={Math.abs(t) < 0.2 ? "muted" : t > 0 ? "up" : "down"}>{t > 0 ? "↑" : t < 0 ? "↓" : "→"}{num(Math.abs(t), 1)}</span>}
            {!stale && <LineSpark values={s.weekly} color={DISC_COLOR[k]} />}
          </a>
        );
      })}
      {pred && <a class="disc" href="/fitness" title={`One race prediction for today: your interval and long-distance shapes blended by how certain each is (a stale shape counts less) · 5k ${mmss(pred["5k"])} · 10k ${dur(pred["10k"])} · half ${dur(pred.half)}`}>
        <span class="nm">Today</span><b>10k ≈ {dur(pred["10k"])}</b>
      </a>}
      <a class="more" href="/fitness">Fitness →</a>
    </div>
  );
}

// ------------------------------------------------------------------ latest day
function LatestDay({ h }: { h: Home }) {
  const d = h.latest_day!;
  const ago = daysBetween(d.date, h.anchor);
  const sm = d.summary, sl = d.sleep;
  const cols: Col<Activity>[] = [
    { key: "start", label: "Start", render: (a) => a.start.slice(11, 16) },
    { key: "sport", label: "Sport", render: (a) => SPORT_LABEL[a.sport] ?? a.sport },
    { key: "w", label: "Workout", sort: () => null, render: (a) => (
      <span class="clip" style={{ maxWidth: "240px" }} title={workoutTip(a)}>{workoutText(a)}</span>) },
    { key: "dist_km", label: "km", num: true, render: (a) => <DistCell a={a} />, sort: distSort },
    { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s ?? a.dur_s) },
    { key: "speed", label: "Pace", num: true, sort: () => null, title: "Moving pace: min/km on foot, /100 m for swims, km/h for ski, bike and other", render: (a) => speedText(a) },
    { key: "hr", label: "HR", num: true, title: "Average heart rate (bpm)", render: (a) => num(a.hr) },
    { key: "place", label: "Place", render: (a) => { const p = a.place_name ?? a.place ?? a.location ?? "—"; return <span class="clip" style={{ maxWidth: "160px" }} title={p}>{p}</span>; } },
  ];
  // one muted line; values that are missing are left out, and a summary / sleep from another day says which day
  const ok = (v: number | null | undefined): v is number => v !== null && v !== undefined && !isNaN(v);
  const smOther = sm && d.summary_date && d.summary_date !== d.date ? ` (${shortDate(d.summary_date, h.anchor)})` : "";
  const slOther = sl && d.sleep_date && d.sleep_date !== d.date ? ` (${shortDate(d.sleep_date, h.anchor)})` : "";
  const parts = [
    sm && ok(sm.rhr) ? `RHR ${num(sm.rhr)}` : "",
    sm && ok(sm.stress) ? `Stress ${num(sm.stress)}` : "",
    sm && ok(sm.bbmin) && ok(sm.bbmax) ? `Battery ${num(sm.bbmin)}–${num(sm.bbmax)}` : "",
    sm && ok(sm.steps) ? `${num(sm.steps)} steps` : "",
    sl && ok(sl.total) ? `Sleep ${hm(sl.total)} h${ok(sl.score) ? ` (score ${num(sl.score)})` : ""}${slOther}` : "",
  ].filter(Boolean);
  const smParts = parts.length - (sl && ok(sl.total) ? 1 : 0);
  if (smOther && smParts > 0) parts[smParts - 1] += smOther;
  return (
    <section class="first">
      <div class="head">
        <h2>{ago === 0 ? "Today" : ago === 1 ? "Yesterday" : "Latest active day"} · {shortDate(d.date, h.anchor)}</h2>
        <span class="sub">{ago > 1 ? `${ago} days ago · ` : ""}{d.activities.length} {d.activities.length === 1 ? "activity" : "activities"},{" "}
          {km(kmTotal(d.activities).km)} km, {hm(d.activities.reduce((s, a) => s + (a.moving_s || a.dur_s || 0), 0))} h</span>
        <div class="tools"><a class="btn" href={`/day/${d.date}`}>Day view</a></div>
      </div>
      <Table cols={cols} rows={d.activities} rowKey={(a) => a.id} onRow={(a) => navigate(`/activities/${a.id}`)} stickyCols={0} />
      {parts.length > 0 && <p class="day-line muted" title="Daily summary: resting heart rate, average stress, body battery low–high, steps, sleep">{parts.join(" · ")}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ training log (5 weeks)
const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function TrainingLog({ h, hover, setHover }: { h: Home; hover: string | null; setHover: (id: string | null) => void }) {
  const rows = useMemo(() => new Map(h.recent.map((r) => [r.id, r])), [h]);
  const start = h.periods.log[0];
  const weeks = Array.from({ length: Math.round((daysBetween(start, h.periods.log[1]) + 1) / 7) }, (_, w) =>
    Array.from({ length: 7 }, (_, i) => addDays(start, w * 7 + i)));
  const monthFrom = h.periods.month[0];
  return (
    <div class="tlog">
      <div class="tl-head">{WD.map((d) => <span key={d}>{d}</span>)}<span class="num">Week</span></div>
      {weeks.map((wk) => {
        const ids = wk.flatMap((d) => h.days[d] ?? []);
        const acts = ids.map((id) => rows.get(id)).filter((r): r is HomeRow => !!r);
        const tkm = acts.reduce((s, r) => s + trustedKm(r), 0), ts = acts.reduce((s, r) => s + (r.moving_s || r.dur_s || 0), 0);
        const bySport: Record<string, number> = {};
        for (const r of acts) bySport[r.sport] = (bySport[r.sport] ?? 0) + (r.moving_s || r.dur_s || 0);
        return (
          <div class="tl-week" key={wk[0]}>
            {wk.map((d) => {
              const future = d > h.anchor, ids = h.days[d] ?? [];
              const cls = "tl-day" + (future ? " future" : "") + (d === h.anchor ? " today" : "") + (d < monthFrom ? " old" : "") + (ids.length ? " on" : "");
              return (
                <div key={d} class={cls} onClick={future ? undefined : () => navigate(`/day/${d}`)} title={future ? "" : `${shortDate(d)} — open the day`}>
                  <span class="dn"><span class="wd">{WD[(new Date(d + "T12:00:00").getDay() + 6) % 7]} </span>{Number(d.slice(8))}{d.slice(8) === "01" || d === wk[0] && wk === weeks[0] ? ` ${shortDate(d).split(" ")[2]}` : ""}</span>
                  {ids.map((id) => {
                    const r = rows.get(id);
                    if (!r) return null;
                    const short = r.workout_label?.includes("×") ? r.workout_label : "";
                    return (
                      <a key={id} href={`/activities/${id}`} class={"tl-act" + (hover === id ? " hl" : "")} onClick={(e) => e.stopPropagation()}
                        onMouseEnter={() => setHover(id)} onMouseLeave={() => setHover(null)}
                        title={`${workoutText(r)} · ${distStr(r, " km")} · ${dur(r.moving_s)}${r.place_name ? " · " + r.place_name : ""}${r.together ? ` · with ${r.together.with.map(personName).join(", ")}` : ""}`}>
                        <span class="dot" style={{ background: sportColor(r.sport) }} />
                        {short ? <>
                          <span class="t full"><SigTokens s={short} /></span>
                          <span class="t short"><SigTokens s={compactSig(short)} /></span>
                        </> : <span class="t">{distStr(r) !== "—" ? <>{distStr(r)}<span class="u">{"\u00a0"}km</span></> : dur(r.moving_s ?? r.dur_s)}</span>}
                      </a>
                    );
                  })}
                </div>
              );
            })}
            <div class="tl-sum">
              <span class="num">{acts.length ? <><b>{km(tkm)}</b> km</> : <span class="muted">—</span>}</span>
              {acts.length > 0 && <span class="muted num">{hm(ts)} h</span>}
              {ts > 0 && <span class="mix" title={Object.entries(bySport).map(([k, v]) => `${SPORT_LABEL[k]} ${hm(v)} h`).join(" · ")}>
                {Object.entries(bySport).map(([k, v]) => <i key={k} style={{ width: `${(v / ts) * 100}%`, background: sportColor(k) }} />)}
              </span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ volume per sport
function Volume({ h, metric }: { h: Home; metric: "km" | "time" }) {
  const v = (a: Agg) => (metric === "km" ? a.km : a.s);
  const f = (x: number) => (metric === "km" ? `${km(x) === "—" ? "0" : km(x)}` : hm(x));
  const unit = metric === "km" ? "km" : "h";
  return (
    <div class="tablewrap vol-wrap">
    <table class="vol">
      <thead>
        <tr class="grp"><th /><th colSpan={2} class="c">Last 7 days</th><th colSpan={2} class="c">Last 30 days</th></tr>
        <tr>
          <th>Sport</th>
          <th class="num" title="Distance or time (hover for the number of activities)">{unit}</th>
          <th title="vs the average week of the 4 weeks before (bar = last 7 days, tick = that average)">vs prev 4 wk</th>
          <th class="num" title="Distance or time (hover for the number of activities)">{unit}</th>
          <th title="vs the average 30 days of the 12 months before (bar = last 30 days, tick = that average)">vs 12-mo avg</th>
        </tr>
      </thead>
      <tbody>
        {h.volume.map((r) => {
          const all = r.sport === "all";
          const col = all ? "var(--text-muted)" : sportColor(r.sport);
          const nAct = (n: number) => `${n} ${n === 1 ? "activity" : "activities"}`;
          // km leave out indoor / no-GPS distances (their time counts)
          const untr = (a: Agg) => (metric === "km" && a.n_untrusted ? ` (${a.n_untrusted} without a trusted distance: not in km)` : "");
          return (
            <tr key={r.sport} class={all ? "total" : ""}>
              <td>{all ? "All" : SPORT_LABEL[r.sport] ?? r.sport}</td>
              <td class="num" title={nAct(r.week.n) + untr(r.week)}>{r.week.n ? f(v(r.week)) : <span class="muted">—</span>}</td>
              <td title={`average week before: ${f(v(r.prev4))} ${unit}, ${num(r.prev4.n, 1)} activities`}>
                <span class="vs"><VsBar v={v(r.week)} typ={v(r.prev4)} color={col} />{pctDelta(v(r.week), v(r.prev4))}</span></td>
              <td class="num" title={nAct(r.month.n) + untr(r.month)}>{r.month.n ? f(v(r.month)) : <span class="muted">—</span>}</td>
              <td title={`average 30 days over the year before: ${f(v(r.year30))} ${unit}, ${num(r.year30.n, 1)} activities`}>
                <span class="vs"><VsBar v={v(r.month)} typ={v(r.year30)} color={col} />{pctDelta(v(r.month), v(r.year30))}</span></td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}

// ------------------------------------------------------------------ recent workouts
function recentCols(anchor: string): Col<HomeRow>[] {
  return [
    { key: "date", label: "Date", sort: (r) => r.start ?? r.date, render: (r) => <span>{shortDate(r.date, anchor)} <span class="muted">{r.start?.slice(11, 16)}</span></span> },
    { key: "sport", label: "Sport", sort: (r) => SPORT_LABEL[r.sport] ?? r.sport, render: (r) => SPORT_LABEL[r.sport] ?? r.sport },
    { key: "w", label: "Workout", title: "“@” = average rep pace of the main reps (total rep distance / total rep time)", sort: (r) => workoutText(r), render: (r) => (
      <span class="clip" style={{ maxWidth: "230px" }} title={workoutTip(r)}>{workoutText(r)}</span>) },
    { key: "rest_s", label: "Rest", num: true, title: "Median rest between the main reps", render: (r) => (r.rest_s ? secs(r.rest_s) : "") },
    { key: "dist_km", label: "km", num: true, render: (r) => <DistCell a={r} />, sort: distSort },
    { key: "moving_s", label: "Moving", num: true, render: (r) => dur(r.moving_s ?? r.dur_s) },
    { key: "speed_ms", label: "Pace", num: true, title: "Moving pace: min/km on foot (run, walk, hike), /100 m for swims, km/h for ski, bike and other",
      sort: (r) => r.speed_ms, render: (r) => speedText({ ...(r as any), hr: r.avg_hr } as Activity) },
    { key: "avg_hr", label: "HR", num: true, title: "Average heart rate (bpm)", render: (r) => num(r.avg_hr) },
    { key: "place", label: "Place", sort: (r) => r.place_name ?? r.place ?? "", render: (r) => { const p = r.place_name ?? r.place ?? "—";
      return <span class="clip" style={{ maxWidth: "110px" }} title={[p, r.route_name].filter(Boolean).join(" · ")}>{p}</span>; } },
  ];
}

// ------------------------------------------------------------------ page
export function HomePage() {
  const { query } = useLocation();
  const metric = query.get("vm") === "time" ? "time" : "km";
  const { data: h, error } = useApi<Home>("/api/home");
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => { document.title = "Home"; }, []);
  const cols = useMemo(() => recentCols(h?.anchor ?? ""), [h]);
  if (error) return <main><PageHeader title="Home" /><div class="err">Could not load the home page: {error}</div></main>;
  if (!h) return <main><div class="empty">Loading…</div></main>;
  const monthRows = h.recent.filter((r) => r.date >= h.periods.month[0]);
  const shownRows = monthRows.length >= 8 ? monthRows : h.recent;
  return (
    <main class="home">
      <PageHeader title="Home" sub={`${shortDate(h.anchor)} ${h.anchor.slice(0, 4)}`} class="home-top"
        about="Your latest training at a glance: the last active day, the last five weeks, volume against your usual, and current shape."
        tips={["Click a day in the training log for the Day view, an activity to open it", "Hover a number in Volume for the activity count", "In Recent workouts, “@” = average rep pace of the main reps (total rep distance / total rep time)",
          "Every view is a URL: copy it to come back to the same state"]}>
        {h.fitness && <ShapeLine fit={h.fitness} />}
      </PageHeader>

      {h.latest_day ? <LatestDay h={h} /> : <section class="first"><div class="empty">No activities yet. Fetch some with <span class="mono">scripts/fetch_index.py</span>.</div></section>}

      <section>
        <div class="home-two">
          <div>
            <div class="head"><h2>Last 5 weeks</h2>
              <div class="tools"><a href={`/day/${h.latest ?? ""}`}>Calendar →</a></div></div>
            <TrainingLog h={h} hover={hover} setHover={setHover} />
          </div>
          <div>
            <div class="head"><h2 title={"Volume per sport vs typical. Bar = this period, tick = typical (average week of the 4 weeks before; average 30 days of the year before). Hover a number for the activity count."
              + (metric === "km" ? " km count trusted (GPS) distances only: indoor and no-GPS sessions count in Time." : "")}>Volume</h2><span class="sub">vs typical</span>
              <div class="tools">
                <div class="seg">
                  <button class={metric === "km" ? "on" : ""} onClick={() => setQuery({ vm: null })}>km</button>
                  <button class={metric === "time" ? "on" : ""} onClick={() => setQuery({ vm: "time" })}>Time</button>
                </div>
              </div>
            </div>
            <Volume h={h} metric={metric} />
          </div>
        </div>
      </section>

      <ProgressCard anchor={h.anchor} />

      <section>
        <div class="head"><h2>Recent workouts</h2>
          <span class="sub">{shownRows === monthRows ? `last 30 days · ${monthRows.length} activities, ${km(kmTotal(monthRows).km)} km` : `last ${shownRows.length}`}</span>
          <div class="tools"><a href={`/activities?from=${h.periods.month[0]}`}>All activities →</a></div></div>
        <Table cols={cols} rows={shownRows} rowKey={(r) => r.id} highlight={hover} onHover={(r) => setHover(r?.id ?? null)}
          onRow={(r) => navigate(`/activities/${r.id}`)} defaultSort={["date", -1]} stickyCols={1} />
      </section>
    </main>
  );
}
