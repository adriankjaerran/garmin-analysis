// Fitness: shape per discipline (interval / long-distance running / ski) and overall over the years,
// from scripts/fitness_model.py (fitness.json via /api/fitness). URL state:
// ?d=interval,long,ski&ov=0&b=0&from=2023-01-01&to=2026-10-31&sd=interval&sk=race&q=
import { useMemo, useState } from "preact/hooks";
import { useApi } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { num } from "../lib/format";
import { Table, type Col } from "../components/Table";
import { ShapeChart } from "../components/ShapeChart";
import "../fitness.css";
import {
  DISCS, DISC_LABEL, KIND_HELP, KIND_LABEL, daysAgo, discColor, pm, raceT, signed, unitShort,
  type Disc, type FitSession, type FitnessDoc, type Kind, type Yearly,
} from "../lib/fitness";

const DOT_CONF: Record<string, string> = { high: "good", medium: "partial", low: "none" };

interface CurRow { key: string; label: string; mean: number; sd: number; conf: string | null; pct: number | null; pb: number | null;
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
    const r = await fetch("/api/fitness/rebuild", { method: "POST" }).then((r) => r.json());
    if (!r.accepted && !r.running) throw new Error(r.error || "not started");
    for (let i = 0; i < 300; i++) {
      await new Promise((ok) => setTimeout(ok, 1000));
      const s = await fetch("/api/fitness/rebuild").then((r) => r.json());
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
  const discs = (query.get("d") ?? "interval,long,ski").split(",").filter((d): d is Disc => (DISCS as string[]).includes(d));
  const overall = query.get("ov") !== "0";
  const bounds = query.get("b") !== "0";
  const from = query.get("from"), to = query.get("to");
  const sd = query.get("sd") ?? "", sk = query.get("sk") ?? "", q = (query.get("q") ?? "").toLowerCase();

  const years = useMemo(() => {
    if (!doc) return [] as string[];
    const ys = new Set<string>();
    for (const d of Object.values(doc.disciplines)) for (const y of Object.keys(d.yearly)) ys.add(y);
    return [...ys].sort();
  }, [doc]);

  const curRows = useMemo<CurRow[]>(() => {
    if (!doc) return [];
    const ref = doc.disciplines.interval?.current.date ?? doc.built.slice(0, 10);
    const rows: CurRow[] = DISCS.filter((k) => doc.disciplines[k]).map((k) => {
      const d = doc.disciplines[k], c = d.current, eq = c.race_equivalents_s;
      return { key: k, label: DISC_LABEL[k], mean: c.mean, sd: c.sd, conf: c.confidence ?? null, pct: c.pct_of_best ?? null,
        pb: d.personal_best ?? null, r5: eq?.["5k"] ?? null, r10: eq?.["10k"] ?? null, rh: eq?.half ?? null, trend: c.trend_90d,
        last: c.last_evidence?.date ?? null, lastId: c.last_evidence ? String(c.last_evidence.id) : null,
        lastDays: c.last_evidence ? daysAgo(c.last_evidence.date, ref) : null };
    });
    const o = doc.disciplines.overall;
    if (o) rows.push({ key: "overall", label: DISC_LABEL.overall, mean: o.current.mean, sd: o.current.sd, conf: null, pct: o.current.mean,
      pb: null, r5: null, r10: null, rh: null, trend: o.current.trend_90d, last: null, lastId: null, lastDays: null });
    return rows;
  }, [doc]);

  const yearRows = useMemo(() => years.map((y) => ({ y, ...Object.fromEntries(
    [...DISCS, "overall"].map((k) => [k, doc?.disciplines[k]?.yearly[y] ?? null])) })) as any[], [doc, years]);

  const sessions = useMemo(() => (doc?.sessions ?? []).filter((s) =>
    (!sd || s.discipline === sd) && (!sk || s.kind === sk) && (!from || s.date >= from) && (!to || s.date <= to) &&
    (!q || `${s.name ?? ""} ${s.type} ${s.explain} ${s.date}`.toLowerCase().includes(q))), [doc, sd, sk, from, to, q]);

  if (error) return <main><h1>Fitness</h1><div class="err">{error}</div></main>;
  if (!doc) return <main><div class="empty">Loading…</div></main>;

  const toggle = (k: Disc) => {
    const next = discs.includes(k) ? discs.filter((d) => d !== k) : DISCS.filter((d) => d === k || discs.includes(d));
    setQuery({ d: next.length === 3 ? null : next.join(",") || "none" });
  };
  const curCols: Col<CurRow>[] = [
    { key: "label", label: "Discipline", render: (r) => <><span class="dot" style={{ background: discColor(r.key) }} />{r.label}</> },
    { key: "mean", label: "Current", num: true, render: (r) => <b>{pm(r.mean, r.sd)}</b>, title: "smoothed shape today ± 1 sd" },
    { key: "unit", label: "Unit", render: (r) => <span class="muted">{unitShort(r.key)}</span>, sort: () => null },
    { key: "conf", label: "Confidence", render: (r) => r.conf ? <><span class={"dot " + DOT_CONF[r.conf]} />{r.conf}</> : <span class="muted">—</span>,
      title: "high: sd < 1, medium: < 2, low: ≥ 2 (no recent evidence)" },
    { key: "pct", label: "% of best", num: true, render: (r) => (r.pct === null ? "—" : `${r.pct.toFixed(1)} %`) },
    { key: "pb", label: "Best", num: true, render: (r) => (r.pb === null ? "—" : r.pb.toFixed(1)), title: "personal best of the smoothed shape" },
    { key: "r5", label: "5k", num: true, render: (r) => r.key === "ski" ? <span class="muted" title="ski is on its own VDOT-equivalent scale; no running times">n/a</span> : raceT(r.r5) },
    { key: "r10", label: "10k", num: true, render: (r) => r.key === "ski" ? <span class="muted">n/a</span> : raceT(r.r10) },
    { key: "rh", label: "Half", num: true, render: (r) => r.key === "ski" ? <span class="muted">n/a</span> : raceT(r.rh) },
    { key: "trend", label: "Trend 90 d", num: true, render: (r) => <span style={{ color: r.trend === null ? undefined : r.trend > 0.3 ? "var(--state-good)" : r.trend < -0.3 ? "var(--state-duplicate)" : undefined }}>{signed(r.trend)}</span>,
      title: "current minus 90 days ago" },
    { key: "last", label: "Last evidence", render: (r) => r.last ? <><a href={`/activities/${r.lastId}`}>{r.last}</a> <span class="muted">{r.lastDays} d ago</span></> : <span class="muted">{r.key === "overall" ? "" : "none"}</span>,
      title: "latest race or hard session with weight > 0.5" },
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
    { key: "discipline", label: "Discipline", render: (s) => <><span class="dot" style={{ background: discColor(s.discipline) }} />{DISC_LABEL[s.discipline]}</> },
    { key: "kind", label: "Kind", render: (s) => <><KindMark kind={s.kind} disc={s.discipline} />{KIND_LABEL[s.kind]}</> },
    { key: "name", label: "Activity", render: (s) => s.name ?? s.type },
    { key: "explain", label: "What it shows", render: (s) => <span title={s.explain}>{s.explain}</span> },
    { key: "score", label: "Score", num: true, render: (s) => s.score.toFixed(1) },
    { key: "shape_at", label: "Shape", num: true, render: (s) => (s.shape_at === null ? "—" : s.shape_at.toFixed(1)), title: "smoothed shape that day" },
    { key: "delta", label: "Δ", num: true, sort: (s) => (s.shape_at === null ? null : s.score - s.shape_at),
      render: (s) => (s.shape_at === null || s.kind === "implausible" ? "—" : signed(s.score - s.shape_at)), title: "score − shape" },
    { key: "weight", label: "Weight", num: true, render: (s) => num(s.weight, 2), title: "robust weight the smoother gave it (0..1)" },
    { key: "sigma", label: "σ", num: true, render: (s) => s.sigma.toFixed(1), title: "observation sd for this kind" },
  ];

  const yFrom = from?.slice(0, 4) ?? "", yTo = to?.slice(0, 4) ?? "";
  return (
    <main>
      <section class="first">
        <div class="head">
          <h1>Fitness</h1>
          <span class="sub">Shape per discipline on the Daniels VDOT scale · model built {doc.built.slice(0, 16).replace("T", " ")} · HR not used in the score</span>
          <div class="tools">
            {rb && <span class="muted">{rb}</span>}
            <button onClick={() => rebuild(setRb)} title="Re-run scripts/fitness_model.py (≈0.1 s with cached features)">Rebuild</button>
          </div>
        </div>
        <Table rows={curRows} rowKey={(r) => r.key} cols={curCols} />
      </section>

      <section>
        <div class="head">
          <h2>Shape over time</h2>
          <span class="sub">weekly, ± 1 sd band; drag to zoom, double-click to reset</span>
          <div class="tools">
            <div class="chips">
              {DISCS.map((k) => (
                <button key={k} class={"chip" + (discs.includes(k) ? " on" : "")} onClick={() => toggle(k)}>
                  <span class="dot" style={{ background: discColor(k) }} />{DISC_LABEL[k]}
                </button>
              ))}
              <button class={"chip" + (overall ? " on" : "")} onClick={() => setQuery({ ov: overall ? "0" : null })}>
                <span class="dot" style={{ background: discColor("overall") }} />Overall %
              </button>
              <button class={"chip" + (bounds ? " on" : "")} onClick={() => setQuery({ b: bounds ? "0" : null })} title={KIND_HELP.bound}>Bounds</button>
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
        <ShapeChart doc={doc} discs={discs} overall={overall} bounds={bounds} from={from} to={to} highlight={hover}
          onZoom={(a, b) => setQuery({ from: a, to: b })} />
        <div class="legend-row muted">
          <span><KindMark kind="race" disc="interval" />race</span>
          <span><KindMark kind="hard" disc="interval" />hard session (paler = lower weight)</span>
          <span><KindMark kind="bound" disc="interval" />lower bound</span>
          <span><KindMark kind="implausible" disc="interval" />implausible</span>
          <span>Ski is on its own VDOT-equivalent scale (physics-based), comparable in trend, not in absolute level.</span>
        </div>
      </section>

      <section>
        <div class="head"><h2>By year</h2><span class="sub">mean and max of the daily shape; n = races + hard sessions; * = 0–2 pieces of evidence (interpolated)</span></div>
        <Table rows={yearRows} rowKey={(r) => r.y} cols={yearCols} defaultSort={["y", -1]} />
      </section>

      <section>
        <div class="head">
          <h2>Sessions</h2><span class="count">{sessions.length}</span>
          <span class="sub">every scored session; hover to find it in the chart, click to open</span>
          <div class="tools">
            <input type="search" placeholder="Filter…" value={query.get("q") ?? ""} onInput={(e) => setQuery({ q: (e.target as HTMLInputElement).value })} />
            <div class="seg">
              {["", ...DISCS].map((k) => <button key={k} class={sd === k ? "on" : ""} onClick={() => setQuery({ sd: k })}>{k ? DISC_LABEL[k] : "All"}</button>)}
            </div>
            <div class="seg">
              {(["", "race", "hard", "bound", "implausible"] as const).map((k) => <button key={k} class={sk === k ? "on" : ""} onClick={() => setQuery({ sk: k })}>{k ? KIND_LABEL[k] : "All kinds"}</button>)}
            </div>
          </div>
        </div>
        <div class="fit-sessions"><Table rows={sessions} rowKey={(s) => s.id + s.discipline} cols={sessCols} defaultSort={["date", -1]} maxHeight="560px"
          onRow={(s) => navigate(`/activities/${s.id}`)} onHover={(s) => setHover(s ? s.id : null)} /></div>
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
              <li><b>Heart rate is not used in the score.</b> It made race prediction worse (MAE 1.86 → 2.37 VDOT) and added noise: the
                wrist sensor under-reads hard intervals erratically and the watch change shifted HR by ~0.17 HRR. HR is shown per session as
                diagnostics only (effort %, efficiency, decoupling).</li>
            </ul>
            <p class="muted">Full model, evaluation and limitations: <span class="mono">docs/fitness-model.md</span>. Engine:
              <span class="mono"> scripts/fitness_model.py</span> → <span class="mono">data/&lt;person&gt;/fitness.json</span>.</p>
          </div>
        </details>
      </section>
    </main>
  );
}
