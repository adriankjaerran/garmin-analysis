// Race predictor: predicted race times for 3k / 5k / 10k / half / marathon (+ one custom distance) on a chosen
// day, from the running shape and the personal distance curve (/api/fitness/predictor + /api/fitness/predict).
// URL state: ?date=YYYY-MM-DD (default = latest day) &km=7.5 (custom distance, 0.8–100 km)
import { useEffect, useState } from "preact/hooks";
import { qs, useApi } from "../lib/api";
import { setQuery, useLocation } from "../lib/router";
import { dur } from "../lib/format";
import { Table, type Col } from "../components/Table";
import { PageHeader } from "../components/PageHeader";
import { PRED_LINES, PredictorChart, type PredictorDoc } from "../components/PredictorChart";
import "../fitness.css";
import "./predictor.css";

interface Prediction { km: number; label: string; vdot: number; sd: number; time_s: number; pace_s_per_km: number;
  range_s: [number, number]; extrapolated: boolean }
interface PredictDoc {
  date: string; predictions: Prediction[];
  shape: { theta: number; slope: number; slope_text: string };
  basis: { last_evidence_date: string | null; days_since_evidence: number | null; n_evidence_90d: number };
}

const STD_KM = [3, 5, 10, 21.0975, 42.195];
const KM_MIN = 0.8, KM_MAX = 100;
/** no race / hard session for more than this many days before the selected day: say the estimate is uncertain */
const UNCERTAIN_DAYS = 60;
const ISO = /^\d{4}-\d{2}-\d{2}$/;

const parseKm = (s: string | null) => {
  if (!s) return null;
  const v = Number(s.replace(",", "."));
  return isFinite(v) && v >= KM_MIN && v <= KM_MAX ? Math.round(v * 1000) / 1000 : null;
};
const kmLabel = (km: number) => `${km} km`;
const sameKm = (a: number, b: number) => Math.abs(a - b) < 1e-6;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

export function RacePredictorPage() {
  const { query } = useLocation();
  const qDate = query.get("date");
  const date = qDate && ISO.test(qDate) ? qDate : null;
  const custom = parseKm(query.get("km"));
  const { data: doc, error } = useApi<PredictorDoc>("/api/fitness/predictor");
  const kms = custom !== null && !STD_KM.some((k) => sameKm(k, custom)) ? [...STD_KM, custom] : STD_KM;
  const { data: pred, error: predErr, loading } = useApi<PredictDoc>(doc ? "/api/fitness/predict" + qs({ date, km: kms.join(",") }) : null);

  // the custom-distance field: free text, committed to the URL when valid
  const [kmText, setKmText] = useState(query.get("km") ?? "");
  useEffect(() => setKmText(query.get("km") ?? ""), [query.get("km")]);
  const kmBad = kmText.trim() !== "" && parseKm(kmText.trim()) === null;
  const commitKm = () => { if (!kmBad) setQuery({ km: kmText.trim() ? String(parseKm(kmText.trim())) : null }); };

  const head = (sub?: string) => (
    <PageHeader title="Race predictor" sub={sub}
      about="Predicted race times on a chosen day, from your running shape and your personal distance curve."
      tips={[
        "Your shape comes from your races and hard sessions, smoothed over time (the chart).",
        "A personal distance curve turns it into a level per distance: how much you slow down as the race gets longer.",
        "The likely range is ±1 sd of the estimate: the actual race should land in it about two times out of three.",
        "Distances beyond your longest race or hard session (usually the marathon) are extrapolated: less certain, with a wider range.",
        "Click the chart or pick a date to see the prediction on that day; Today goes back to the latest.",
      ]} />
  );

  if (error) return (
    <main><section class="first">{head()}
      <div class="empty-state"><p><b>No race prediction available.</b></p>
        <p class="muted">The server said: {error}</p></div>
    </section></main>
  );
  if (!doc) return <main><section class="first">{head()}<div class="empty">Loading…</div></section></main>;

  const first = doc.daily[0]?.date, last = doc.daily[doc.daily.length - 1]?.date;
  const selected = pred?.date ?? date ?? last ?? null;
  const isLatest = !date;
  const longest = doc.evidence_km?.[1];

  const rows = pred ? kms.map((k) => pred.predictions.find((p) => sameKm(p.km, k))).filter((p): p is Prediction => !!p) : [];
  const cols: Col<Prediction>[] = [
    { key: "label", label: "Distance", render: (p) => <>
      {STD_KM.some((k) => sameKm(k, p.km)) ? p.label : kmLabel(p.km)}
      {p.extrapolated && <span class="muted pred-x" title={`beyond your longest race or hard session${longest ? ` (${longest.toFixed(1)} km)` : ""}: extrapolated along your distance curve, so less certain`}> extrapolated</span>}
    </> },
    { key: "time_s", label: "Predicted time", num: true, render: (p) => <b class={p.extrapolated ? "muted" : ""}>{dur(p.time_s)}</b> },
    { key: "pace_s_per_km", label: "Pace /km", num: true, render: (p) => dur(p.pace_s_per_km) },
    { key: "range", label: "Likely range", num: true, title: "fast – slow, ±1 sd of the estimate", sort: (p) => p.range_s[0],
      render: (p) => <span class={p.extrapolated ? "muted" : ""}>{dur(p.range_s[0])} – {dur(p.range_s[1])}</span> },
    { key: "vdot", label: "VDOT", num: true, title: "the level at this distance ± 1 sd", render: (p) => `${p.vdot.toFixed(1)} ± ${p.sd.toFixed(1)}` },
  ];

  const b = pred?.basis;
  const basisText = !b ? null : b.days_since_evidence === null || b.last_evidence_date === null
    ? "no race or hard session before this day"
    : `last race or hard session ${b.days_since_evidence === 0 ? "the same day" : plural(b.days_since_evidence, "day") + " before"}; ${plural(b.n_evidence_90d, "session")} in the 90 days before`;
  const uncertain = !!b && (b.days_since_evidence === null || b.days_since_evidence > UNCERTAIN_DAYS);

  return (
    <main>
      <section class="first">
        {head(doc.last_evidence_date ? `last race or hard session ${doc.last_evidence_date}` : undefined)}
        <div class="head pred-pick">
          <h2>{selected ? `Prediction for ${selected}` : "Prediction"}</h2>
          {isLatest && <span class="sub">latest</span>}
          <div class="tools">
            <input type="date" value={selected ?? ""} min={first} max={last} aria-label="Date"
              onChange={(e) => { const v = (e.target as HTMLInputElement).value; if (ISO.test(v)) setQuery({ date: v === last ? null : v }); }} />
            <button disabled={isLatest} onClick={() => setQuery({ date: null })}>Today</button>
          </div>
        </div>
        <PredictorChart doc={doc} selected={selected} onSelect={(d) => setQuery({ date: d === last ? null : d })} />
        <div class="legend-row muted">
          {PRED_LINES.map((l) => <span key={l.k}><span class="line-sw" style={{ borderTopColor: `var(${l.color})` }} />{l.label}</span>)}
          <span>bands ± 1 sd</span>
          <span><span class="pred-mk race" />race</span>
          <span title="paler = lower weight"><span class="pred-mk" />hard session</span>
          <span>click to pick a day</span>
        </div>
      </section>

      <section>
        <div class="head">
          <h2>Predicted times</h2>
          {loading && <span class="sub">loading…</span>}
          <div class="tools">
            <label class="muted" for="pred-km">Custom distance</label>
            <input id="pred-km" class={"pred-km" + (kmBad ? " bad" : "")} type="text" inputMode="decimal" placeholder="km" value={kmText}
              onInput={(e) => setKmText((e.target as HTMLInputElement).value)} onBlur={commitKm}
              onKeyDown={(e) => { if (e.key === "Enter") commitKm(); }} />
            <span class="muted">km</span>
            {custom !== null && <button onClick={() => setQuery({ km: null })}>Clear</button>}
          </div>
        </div>
        {kmBad && <p class="err pred-err">Enter a distance between {KM_MIN} and {KM_MAX} km.</p>}
        {predErr ? <p class="err">Could not load the prediction: {predErr}</p>
          : !pred ? <div class="empty">Loading…</div>
          : <>
            <Table rows={rows} rowKey={(p) => String(p.km)} cols={cols} />
            {rows.some((p) => p.extrapolated) && <p class="note">Extrapolated: longer than your longest race or hard session{longest ? ` (${longest.toFixed(1)} km)` : ""}, so the time follows your distance curve beyond the evidence and the range is wider.</p>}
            <p class="pred-basis">{pred.shape.slope_text}{basisText ? `; ${basisText}` : ""}.
              {uncertain && <span class="muted"> No recent race or hard session, so this estimate is uncertain.</span>}</p>
          </>}
      </section>
    </main>
  );
}
