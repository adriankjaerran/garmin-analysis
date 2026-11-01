// Fitness: shape per discipline (interval / long-distance running / ski) and overall over the years,
// from scripts/fitness_model.py (fitness.json via /api/fitness). URL state:
// Two model variants: "pace" (performance only; top-level disciplines) and "hr" (variants.hr: HR decides how
// maximal each hard session was), compared with fitness_eval.json (eval_variants).
// ?d=interval,long,ski&ov=0&b=1&hr=0&from=2023-01-01&to=2026-10-31&sd=interval&sk=race&q=
// (b=1: show lower bounds in the chart, default off; sk: default races + hard sessions; sk=all = every kind).
// #records = the personal records section
import { useMemo, useState } from "preact/hooks";
import { apiUrl, useApi } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { num } from "../lib/format";
import { Table, type Col } from "../components/Table";
import { PageHeader } from "../components/PageHeader";
import { ShapeChart } from "../components/ShapeChart";
import { RecordsSection } from "../components/ProgressCard";
import "../fitness.css";
import {
  DISCS, DISC_LABEL, KIND_HELP, KIND_LABEL, daysAgo, discColor, pm, raceT, signed, unitShort,
  type Disc, type EvalDisc, type FitSession, type FitnessDoc, type Kind, type Yearly,
} from "../lib/fitness";

/** a shape without a race / hard session for more than this many days is greyed */
const STALE_DAYS = 90;

interface CurRow { key: string; label: string; mean: number; sd: number; hrMean: number | null; hrSd: number | null; conf: string | null; pct: number | null; pb: number | null;
  r5: number | null; r10: number | null; rh: number | null; trend: number | null; last: string | null; lastId: string | null; lastDays: number | null }

export function KindMark({ kind, disc }: { kind: Kind; disc: string }) {
  const c = discColor(disc);
  const st = kind === "race" ? { background: c, transform: "rotate(45deg)", borderRadius: 0, width: "7px", height: "7px" }
    : kind === "hard" ? { background: c } : kind === "bound" ? { border: `1.5px solid ${c}`, background: "transparent" }
    : { background: "var(--state-failed)" };
  return <span class="dot" style={st} title={KIND_HELP[kind]} />;
}

async function rebuild(set: (s: string) => void) {
  set("Rebuilding…");
  try {
    const r = await fetch(apiUrl("/api/fitness/rebuild"), { method: "POST" }).then((r) => r.json());
    if (!r.accepted && !r.running) throw new Error(r.error || "not started");
    for (let i = 0; i < 300; i++) {
      await new Promise((ok) => setTimeout(ok, 1000));
      const s = await fetch(apiUrl("/api/fitness/rebuild")).then((r) => r.json());
      if (!s.running) {
        if (s.error) { set("Rebuild failed: " + s.error); return; }
        set(`Rebuilt in ${s.seconds} s, reloading…`);
        location.reload();
        return;
      }
    }
  } catch (e) { set("Rebuild failed: " + String((e as Error).message || e)); }
}

export function FitnessPage() {
  const { query } = useLocation();
  const { data: doc, error } = useApi<FitnessDoc>("/api/fitness");
  const [hover, setHover] = useState<string | null>(null);
  const [rb, setRb] = useState<string | null>(null);
  const [allSess, setAllSess] = useState(false);   // the session list grows with the page: the newest 50 first
  const discs = (query.get("d") ?? "interval,long,ski").split(",").filter((d): d is Disc => (DISCS as string[]).includes(d));
  const overall = query.get("ov") !== "0";
  const bounds = query.get("b") === "1";
  const hasHr = !!doc?.variants?.hr?.disciplines;
  const withHr = hasHr && query.get("hr") !== "0";
  const from = query.get("from"), to = query.get("to");
  // session kind filter: default = races + hard sessions (the evidence that moves the shape); "all" = every kind
  const sd = query.get("sd") ?? "", sk = query.get("sk") ?? "rh", q = (query.get("q") ?? "").toLowerCase();

  const years = useMemo(() => {
    if (!doc) return [] as string[];
    const ys = new Set<string>();
    for (const d of Object.values(doc.disciplines)) for (const y of Object.keys(d.yearly)) ys.add(y);
    return [...ys].sort();
  }, [doc]);

  const curRows = useMemo<CurRow[]>(() => {
    if (!doc) return [];
    const ref = doc.disciplines.interval?.current.date ?? doc.built.slice(0, 10);
    const H = doc.variants?.hr?.disciplines;
    const rows: CurRow[] = DISCS.filter((k) => doc.disciplines[k]).map((k) => {
      const d = doc.disciplines[k], c = d.current, eq = c.race_equivalents_s;
      return { key: k, label: DISC_LABEL[k], mean: c.mean, sd: c.sd, hrMean: H?.[k]?.current.mean ?? null, hrSd: H?.[k]?.current.sd ?? null, conf: c.confidence ?? null, pct: c.pct_of_best ?? null,
        pb: d.personal_best ?? null, r5: eq?.["5k"] ?? null, r10: eq?.["10k"] ?? null, rh: eq?.half ?? null, trend: c.trend_90d,
        last: c.last_evidence?.date ?? null, lastId: c.last_evidence ? String(c.last_evidence.id) : null,
        lastDays: c.last_evidence ? daysAgo(c.last_evidence.date, ref) : null };
    });
    // one race prediction from the distance curve (fitness.json overall.current.race_prediction; vdot/sd at 10k)
    const pr = (doc.disciplines.overall?.current as any)?.race_prediction as { vdot: number; sd: number;
      race_equivalents_s: Record<string, number> } | null | undefined;
    if (pr) rows.push({ key: "pred", label: "Race prediction", mean: pr.vdot, sd: pr.sd, hrMean: null, hrSd: null, conf: null, pct: null, pb: null,
      r5: pr.race_equivalents_s["5k"], r10: pr.race_equivalents_s["10k"], rh: pr.race_equivalents_s.half, trend: null,
      last: "VDOT at 10k, from the distance curve", lastId: null, lastDays: null });
    const o = doc.disciplines.overall;
    if (o) rows.push({ key: "overall", label: DISC_LABEL.overall, mean: o.current.mean, sd: o.current.sd,
      hrMean: H?.overall?.current.mean ?? null, hrSd: H?.overall?.current.sd ?? null, conf: null, pct: o.current.mean,
      pb: null, r5: null, r10: null, rh: null, trend: o.current.trend_90d, last: null, lastId: null, lastDays: null });
    return rows;
  }, [doc]);

  const yearRows = useMemo(() => years.map((y) => ({ y, ...Object.fromEntries(
    [...DISCS, "overall"].map((k) => [k, doc?.disciplines[k]?.yearly[y] ?? null])) })) as any[], [doc, years]);

  // sessions the with-HR variant demoted (hard -> lower bound because the HR shows a sub-maximal effort)
  const demoted = useMemo(() => (doc?.sessions ?? []).filter((s) => s.hr?.note).sort((a, b) => b.date.localeCompare(a.date)), [doc]);

  const sessions = useMemo(() => (doc?.sessions ?? []).filter((s) =>
    (!sd || s.discipline === sd) && (sk === "all" || (sk === "rh" ? s.kind === "race" || s.kind === "hard" : s.kind === sk)) && (!from || s.date >= from) && (!to || s.date <= to) &&
    (!q || `${s.name ?? ""} ${s.type} ${s.explain} ${s.date}`.toLowerCase().includes(q))), [doc, sd, sk, from, to, q]);

  if (error) return (
    <main><PageHeader title="Fitness" />
      <div class="empty-state"><p><b>No fitness estimate yet.</b></p>
        <p class="muted">{/fitness\.json/.test(error) ? "The model hasn't been built for this person. Build it once (≈ 10 s):" : `The server said: ${error}`}</p>
        <button onClick={() => rebuild((m) => alert(m))}>Build the fitness model</button></div>
    </main>
  );
  if (!doc) return <main><div class="empty">Loading…</div></main>;

  const toggle = (k: Disc) => {
    const next = discs.includes(k) ? discs.filter((d) => d !== k) : DISCS.filter((d) => d === k || discs.includes(d));
    setQuery({ d: next.length === 3 ? null : next.join(",") || "none" });
  };
  // stale: no race / hard session that counted for > STALE_DAYS (the model carries the value forward, its sd grows)
  const stale = (r: CurRow) => DISCS.includes(r.key as Disc) && (r.lastDays === null || r.lastDays > STALE_DAYS);
  // hover text of the Current cell: the columns that used to be visible (with HR, unit, % of best, best, confidence)
  const curTitle = (r: CurRow) => [
    `${pm(r.mean, r.sd)} ${unitShort(r.key)}`,
    hasHr && r.hrMean !== null ? `with HR: ${pm(r.hrMean, r.hrSd)}` : null,
    r.pct !== null && r.key !== "overall" ? `${r.pct.toFixed(1)} % of best` : null,
    r.pb !== null ? `best: ${r.pb.toFixed(1)}` : null,
    r.conf ? `confidence: ${r.conf}${r.conf === "low" ? " (sd ≥ 2, no recent evidence)" : ""}` : null,
    stale(r) ? `no race or hard session for ${r.lastDays === null ? "ever" : `${r.lastDays} days`}: the value is carried forward and its uncertainty grows` : null,
  ].filter(Boolean).join("\n");
  const curCols: Col<CurRow>[] = [
    { key: "label", label: "Discipline", render: (r) => r.key === "pred"
      ? <span title="one prediction for today: the interval and long-distance shapes averaged by how certain each is (1/sd²); a shape without recent evidence has a wide band and counts less. The band includes the disagreement between the two.">{r.label}</span>
      : r.label },
    { key: "mean", label: "Current", num: true, render: (r) => (
      <span class={stale(r) ? "muted" : ""} title={curTitle(r)}>
        <b>{pm(r.mean, r.sd)}</b>{r.key === "overall" ? " %" : ""}{r.conf === "low" ? <span class="muted">*</span> : ""}
      </span>),
      title: `smoothed shape today ± 1 sd (${doc.variants?.pace?.label ?? "performance only"}); * = low confidence. Hover a value for with-HR, unit, % of best and best` },
    { key: "trend", label: "Trend", num: true, render: (r) => signed(r.trend),
      title: "current minus 90 days ago" },
    { key: "r5", label: "5k", num: true, render: (r) => r.key === "ski" ? <span class="muted" title="ski is on its own VDOT-equivalent scale; no running times">—</span> : raceT(r.r5) },
    { key: "r10", label: "10k", num: true, render: (r) => r.key === "ski" ? <span class="muted">—</span> : raceT(r.r10) },
    { key: "rh", label: "Half", num: true, render: (r) => r.key === "ski" ? <span class="muted">—</span> : raceT(r.rh) },
    { key: "last", label: "Last evidence", render: (r) => r.key === "pred" ? <span class="muted" title="share of each shape in the blended prediction">{r.last}</span>
      : r.last ? <><a href={`/activities/${r.lastId}`}>{r.last}</a> <span class="muted">{r.lastDays} d ago</span></> : <span class="muted">{r.key === "overall" ? "" : "none"}</span>,
      title: "latest race or hard session with weight > 0.5 (prediction row: the weight of each shape)" },
  ];

  const yc = (k: string, label: string): Col<any>[] => [
    { key: k + "_m", label: `${label} mean`, num: true, sort: (r) => (r[k] as Yearly | null)?.mean,
      render: (r) => { const v = r[k] as Yearly | null; if (!v) return "—"; return <span class={v.n_evidence <= 2 ? "muted" : ""} title={v.n_evidence <= 2 ? "0–2 pieces of evidence: interpolated, wide band" : undefined}>{v.mean.toFixed(1)}{v.n_evidence <= 2 ? "*" : ""}</span>; } },
    { key: k + "_x", label: "max", num: true, sort: (r) => r[k]?.max, render: (r) => (r[k] ? r[k].max.toFixed(1) : "—") },
    { key: k + "_n", label: "n", num: true, sort: (r) => r[k]?.n_evidence, render: (r) => (r[k] ? r[k].n_evidence : "—"), title: "race + hard evidence that year" },
  ];
  const yearCols: Col<any>[] = [
    { key: "y", label: "Year" },
    ...yc("interval", "Interval"), ...yc("long", "Long"), ...yc("ski", "Ski"),
    { key: "overall", label: "Overall", num: true, render: (r) => (r.overall === null ? "—" : `${(r.overall as number).toFixed(1)} %`) },
  ];

  const sessCols: Col<FitSession>[] = [
    { key: "date", label: "Date" },
    { key: "discipline", label: "Discipline", render: (s) => DISC_LABEL[s.discipline] },
    { key: "kind", label: "Kind", render: (s) => <span title={KIND_HELP[s.kind]}>{KIND_LABEL[s.kind]}</span> },
    { key: "name", label: "Activity", render: (s) => <span class="clip" style={{ maxWidth: "200px" }} title={s.name ?? s.type}>{s.name ?? s.type}</span> },
    { key: "explain", label: "What it shows", render: (s) => <span class="wrap-cell">{s.explain}</span> },
    { key: "score", label: "Score", num: true, render: (s) => <span title={`model weight ${num(s.weight, 2)} (0–1), σ ${s.sigma.toFixed(1)}`}>{s.score.toFixed(1)}</span>,
      title: "fitness score of the session (VDOT; ski: VDOT-equivalent); hover for the model weight" },
    { key: "shape_at", label: "Your shape", num: true, render: (s) => (s.shape_at === null ? "—" : s.shape_at.toFixed(1)), title: "your smoothed shape that day" },
    { key: "delta", label: "vs shape", num: true, sort: (s) => (s.shape_at === null ? null : s.score - s.shape_at),
      render: (s) => { if (s.shape_at === null || s.kind === "implausible") return "—"; const d = s.score - s.shape_at, r = Math.abs(d).toFixed(1);
        if (s.kind === "bound" && d < 0) return <span class="muted" title={`${r} below: a lower bound (easy / long run) only counts when it is above your shape`}>easy: no effect</span>;
        return r === "0.0" ? "level" : `${r} ${d > 0 ? "above" : "below"}`; }, title: "score compared with your shape that day (lower bounds below it: no effect)" },
  ];

  const yFrom = from?.slice(0, 4) ?? "", yTo = to?.slice(0, 4) ?? "";
  return (
    <main>
      <section class="first">
        <PageHeader title="Fitness" sub={`updated ${doc.built.slice(0, 10)}`}
          about="Your estimated running and ski shape over time (VDOT: one number ↔ equivalent race times), from your races and hard sessions."
          tips={["Hover a marker in the chart for the session; click it to open the activity", "Drag across the chart to zoom, double-click to reset",
            "Toggle disciplines with the chips above the chart", "“How this is computed” at the bottom explains the model"]}>
          {rb && <span class="muted">{rb}</span>}
          <button onClick={() => rebuild(setRb)} title="Re-run the fitness model with the latest activities (≈ 0.1 s)">Rebuild</button>
        </PageHeader>
        <Table rows={curRows} rowKey={(r) => r.key} cols={curCols} />
      </section>

      <section>
        <div class="head">
          <h2>Shape over time</h2>
          <span class="sub">weekly, ± 1 sd band</span>
          <div class="tools">
            <div class="chips">
              {DISCS.map((k) => (
                <button key={k} class={"chip" + (discs.includes(k) ? " on" : "")} onClick={() => toggle(k)}
                  title={k === "ski" ? "Ski is on its own VDOT-equivalent scale (physics-based): comparable in trend, not in absolute level" : undefined}>
                  <span class="dot" style={{ background: discColor(k) }} />{DISC_LABEL[k]}
                </button>
              ))}
              <button class={"chip" + (overall ? " on" : "")} onClick={() => setQuery({ ov: overall ? "0" : null })}>
                <span class="dot" style={{ background: discColor("overall") }} />Overall %
              </button>
              <button class={"chip" + (bounds ? " on" : "")} onClick={() => setQuery({ b: bounds ? "0" : null })} title={KIND_HELP.bound}>Bounds</button>
              {hasHr && <button class={"chip" + (withHr ? " on" : "")} onClick={() => setQuery({ hr: withHr ? "0" : null })}
                title={doc.variants?.hr?.label}><span class="line-sw dash" />With HR</button>}
            </div>
            <span class="muted">Years</span>
            <select value={yFrom} onChange={(e) => setQuery({ from: (e.target as HTMLSelectElement).value ? (e.target as HTMLSelectElement).value + "-01-01" : null })}>
              <option value="">first</option>
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
            <span class="muted">to</span>
            <select value={yTo} onChange={(e) => setQuery({ to: (e.target as HTMLSelectElement).value ? (e.target as HTMLSelectElement).value + "-12-31" : null })}>
              <option value="">now</option>
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
            <button disabled={!from && !to} onClick={() => setQuery({ from: null, to: null })}>All years</button>
          </div>
        </div>
        <ShapeChart doc={doc} discs={discs} overall={overall} bounds={bounds} from={from} to={to} highlight={hover} withHr={withHr}
          onZoom={(a, b) => setQuery({ from: a, to: b })} />
        {withHr && <div class="legend-row muted">
          <span><span class="line-sw" />{doc.variants?.pace?.label ?? "Performance only"} (± 1 sd band)</span>
          <span><span class="line-sw dash" />{doc.variants?.hr?.label ?? "With HR"}</span>
        </div>}
        <div class="legend-row muted">
          <span><KindMark kind="race" disc="interval" />race</span>
          <span title="paler = lower weight"><KindMark kind="hard" disc="interval" />hard session</span>
          <span><KindMark kind="bound" disc="interval" />lower bound</span>
          <span><KindMark kind="implausible" disc="interval" />implausible</span>
        </div>
      </section>

      <RecordsSection anchor={doc.built.slice(0, 10)} />

      <section>
        <details class="howto">
          <summary title="mean and max of the daily shape; n = races + hard sessions; * = 0–2 pieces of evidence (interpolated)"><h2>By year</h2></summary>
          <Table rows={yearRows} rowKey={(r) => r.y} cols={yearCols} defaultSort={["y", -1]} />
        </details>
      </section>

      <section>
        <div class="head">
          <h2>Sessions</h2><span class="count">{sessions.length}</span>
          <div class="tools">
            <input type="search" placeholder="Filter…" value={query.get("q") ?? ""} onInput={(e) => setQuery({ q: (e.target as HTMLInputElement).value })} />
            <div class="seg">
              {["", ...DISCS].map((k) => <button key={k} class={sd === k ? "on" : ""} onClick={() => setQuery({ sd: k })}>{k ? DISC_LABEL[k] : "All"}</button>)}
            </div>
            <div class="seg">
              {(["rh", "race", "hard", "bound", "implausible", "all"] as const).map((k) => <button key={k} class={sk === k ? "on" : ""} onClick={() => setQuery({ sk: k === "rh" ? null : k })}
                title={k === "rh" ? "races and hard sessions: the evidence that sets your shape" : k === "bound" ? KIND_HELP.bound : undefined}>{k === "rh" ? "Races + hard" : k === "all" ? "All kinds" : KIND_LABEL[k]}</button>)}
            </div>
          </div>
        </div>
        <div class="fit-sessions"><Table rows={allSess ? sessions : [...sessions].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 50)} rowKey={(s) => s.id + s.discipline} cols={sessCols} defaultSort={["date", -1]}
          onRow={(s) => navigate(`/activities/${s.id}`)} onHover={(s) => setHover(s ? s.id : null)} empty="No sessions match these filters." /></div>
        {!allSess && sessions.length > 50 && <p class="note">Showing the newest 50 · <button class="link" onClick={() => setAllSess(true)}>show all {sessions.length}</button></p>}
      </section>

      <section>
        <details class="howto">
          <summary><h2>How this is computed</h2></summary>
          <div class="prose">
            <p>Each session becomes evidence for one discipline, on the <b>Daniels VDOT</b> scale (one number ↔ equivalent race times;
              VDOT 55 ≈ 5k 18:22, 10k 38:06).</p>
            <ul>
              <li><b>Interval</b>: critical-speed / W′-balance model. CS and D′ follow from VDOT; W′ drains above CS and recovers in the
                rests (Skiba τ × {doc.params.tau_scale}). The score is the lowest VDOT for which the whole session was just possible,
                so <i>the same pace on shorter rest scores higher</i>. Offset {doc.params.interval_offset} calibrated on races. Races ≤ 7 km count here too.</li>
              <li><b>Long distance</b>: best grade-adjusted 20–90 min window; tempo runs are hard evidence (+{doc.params.tempo_offset}),
                races 10k+ are races, every other run is only a lower bound. Long evidence far above the interval shape is marked implausible.</li>
              <li><b>Ski</b>: mechanical power (climb + rolling resistance + air drag) → O₂ demand at efficiency {doc.params.ski_eff},
                best 20–90 min of moving time. Same unit, but the absolute level depends on the assumed efficiency.</li>
              <li><b>Smoothing</b>: a daily random walk (Kalman + RTS smoother) with robust, asymmetric weights: a training session far below
                the shape is weak evidence (it may not have been all-out), a race counts fully. In gaps the band widens.</li>
              <li><b>Overall</b>: % of personal best per discipline, precision-weighted across disciplines (ski and run VDOT are never averaged).</li>
              <li><b>With HR</b> (dashed line): the same model, but a hard session whose heart rate shows a sub-maximal effort
                becomes a lower bound, and the other hard sessions are weighted by the probability that they were maximal (p_max).
                “Performance only vs with heart rate” compares both variants on held-out data.</li>
              <li><b>Heart rate as a direct score correction</b> was tested and made race prediction worse (MAE 1.86 → 2.37 VDOT): the
                FR935 wrist sensor under-reads hard intervals erratically and the watch change shifted HR by ~0.17 HRR. So HR only judges
                intent in the With HR variant, and FR935 HR alone can never demote a session. Settings are tuned on few cases (in-sample).</li>
            </ul>
            <p class="muted">Full model, evaluation and limitations: <span class="mono">docs/fitness-model.md</span>. Engine:
              <span class="mono"> scripts/fitness_model.py</span> → <span class="mono">data/&lt;person&gt;/fitness.json</span>.</p>
          </div>
        </details>
      </section>

      {hasHr && <section>
        <details class="howto">
          <summary title="held-out evaluation of both model variants (scripts/fitness_eval.py); the better value is bold"><h2>Performance only vs with heart rate</h2></summary>
          <VariantSection doc={doc} demoted={demoted} />
        </details>
      </section>}
    </main>
  );
}

type Better = "low" | "high";
const EVAL_COLS: { key: keyof EvalDisc; label: string; better: Better; d: number; title: string }[] = [
  { key: "race_mae", label: "Race MAE", better: "low", d: 2, title: "mean absolute error predicting race VDOT from the shape before the race, in VDOT (lower is better; interval / long only)" },
  { key: "forecast_mae", label: "Forecast MAE", better: "low", d: 2, title: "mean absolute error of the next hard session / race from the prior shape, in VDOT (lower is better)" },
  { key: "loglik", label: "Log-lik", better: "high", d: 3, title: "mean predictive log-likelihood of the next evidence (higher = better calibrated)" },
  { key: "r3_corr_prior_shape_vs_log_speed", label: "R3 corr", better: "high", d: 3,
    title: "ski: correlation of the prior shape with log speed on the R3 Holmenkollen climb (independent check; higher is better)" },
];

/** "Performance vs with-HR": eval comparison (fitness_eval.json variants.default) + the sessions HR demoted */
function VariantSection({ doc, demoted }: { doc: FitnessDoc; demoted: FitSession[] }) {
  const ev = doc.eval_variants?.default;
  const pl = doc.variants?.pace?.label ?? "Performance only", hl = doc.variants?.hr?.label ?? "With HR";
  const cell = (key: string, k: (typeof EVAL_COLS)[number], a?: number, b?: number, mine?: number) => {
    if (mine === undefined || mine === null) return <td key={key} class="num muted">—</td>;
    const other = mine === a ? b : a;
    const win = other !== undefined && other !== null && mine !== other && (k.better === "low" ? mine < other : mine > other);
    return <td key={key} class={"num" + (win ? " better" : "")}>{mine.toFixed(k.d)}</td>;
  };
  return (
    <div class="variant-sec">
      <div class="cols" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <div>
          {ev ? (
            <div class="tablewrap">
              <table class="variant-eval">
                <thead>
                  <tr><th rowSpan={2}>Discipline</th>{EVAL_COLS.map((k) => <th key={k.key} colSpan={2} class="num grp" title={k.title}>{k.label}</th>)}</tr>
                  <tr>{EVAL_COLS.map((k) => [<th key={k.key + "p"} class="num" title={pl}>perf.</th>,
                    <th key={k.key + "h"} class="num" title={hl}>HR</th>])}</tr>
                </thead>
                <tbody>
                  {DISCS.filter((d) => ev.pace?.[d] || ev.hr?.[d]).map((d) => {
                    const p = ev.pace?.[d], h = ev.hr?.[d];
                    return (
                      <tr key={d}>
                        <td>{DISC_LABEL[d]} <span class="muted">n {p?.n ?? h?.n}</span></td>
                        {EVAL_COLS.map((k) => {
                          const a = p?.[k.key] as number | undefined, b = h?.[k.key] as number | undefined;
                          return [cell(k.key + "p", k, a, b, a), cell(k.key + "h", k, a, b, b)];
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <div class="empty">No evaluation yet: run <span class="mono">uv run scripts/fitness_eval.py</span>.</div>}
        </div>
        <div>
          <h3 class="muted" style={{ margin: "0 0 var(--space-1)", fontSize: "var(--fs-13)" }}>Demoted by HR <span class="count">{demoted.length}</span></h3>
          {demoted.length ? (
            <div class="tablewrap">
              <table>
                <thead><tr><th>Date</th><th>Type</th><th class="num">Score</th><th class="num" title="probability the session was maximal, from HR">p max</th><th>Note</th></tr></thead>
                <tbody>
                  {demoted.map((s) => (
                    <tr key={s.id + s.discipline}>
                      <td><a href={`/activities/${s.id}`}>{s.date}</a></td>
                      <td>{s.type.replace(/_/g, " ")}</td>
                      <td class="num">{s.score.toFixed(1)}</td>
                      <td class="num">{s.hr?.p_max === null || s.hr?.p_max === undefined ? "—" : s.hr.p_max.toFixed(2)}</td>
                      <td class="muted wrap-cell">{s.hr?.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div class="empty">No session demoted.</div>}
        </div>
      </div>
    </div>
  );
}
