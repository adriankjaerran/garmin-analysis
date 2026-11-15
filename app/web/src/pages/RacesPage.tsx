// Races: the person's official races (data/races.json) against the prediction made from data before race day
// (/api/races; precomputed in fitness.json `races`, docs/fitness-model.md). Run races: official vs predicted time and
// the index (+ = faster than predicted), shown in % or as a time (toggle, remembered). Ski races: results only, with
// the other people's times in the same race.
import { useEffect } from "preact/hooks";
import { useApi } from "../lib/api";
import { dur } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { distLabel, useIdxMode, type RaceRow, type RacesDoc } from "../lib/races";
import { Table, type Col } from "../components/Table";
import { PageHeader } from "../components/PageHeader";
import { IdxCell, IdxToggle, gapCell } from "../components/RaceBits";
import "./races.css";

const key = (r: { name: string; date: string }) => `${r.date} ${r.name}`;

export function RacesPage() {
  const { data, error } = useApi<RacesDoc>("/api/races");
  const [mode, setMode] = useIdxMode();
  useEffect(() => { document.title = "Races"; }, []);

  const head = (sub?: string, tools?: preact.ComponentChildren) => (
    <PageHeader title="Races" sub={sub}
      about="Your official races against what the race predictor would have said the day before."
      tips={[
        "The prediction for each race is made only from data before race day: the race itself and everything after it are left out, as if the predictor had been asked the evening before.",
        "Index = (predicted − official) / predicted. Positive (green) = faster than predicted, negative (red) = slower; near 0 is grey. The time view shows official − predicted (−0:34 = 34 s faster).",
        "Official times come from data/races.json (chip times, or the watch time where no chip time was given).",
        "Ski races have no prediction: the ski model is too coarse to predict a race time (snow, wax, course and drafting matter more than shape), so they show results only, with the others in the same race.",
      ]}>{tools}</PageHeader>
  );
  if (error) return <main><section class="first">{head()}<p class="err">Could not load races: {error}</p></section></main>;
  if (!data) return <main><section class="first">{head()}<div class="empty">Loading…</div></section></main>;

  const run = data.races.filter((r) => r.sport === "run").reverse();
  const ski = data.races.filter((r) => r.sport === "ski").reverse();
  const s = data.summary;
  const best = s.best ? key(s.best) : null;
  const cols: Col<RaceRow>[] = [
    { key: "date", label: "Date" },
    { key: "name", label: "Race", render: (r) => <>
      {r.activity_id ? <a href={`/activities/${r.activity_id}`}>{r.name}</a> : r.name}
      {key(r) === best && s.best!.pct > 0 && <span class="rx-best">★ best</span>}</> },
    { key: "km", label: "Distance", render: (r) => distLabel(r.km) },
    { key: "predicted_s", label: "Predicted", num: true,
      title: "from data before race day; hover for the ±1 sd range",
      render: (r) => r.predicted_s === undefined ? <span class="muted">—</span>
        : <span title={r.predicted_range_s ? `likely ${dur(r.predicted_range_s[0])} – ${dur(r.predicted_range_s[1])} · VDOT ${r.predicted_vdot?.toFixed(1)} ± ${r.predicted_sd?.toFixed(1)} (race: ${r.actual_vdot?.toFixed(1)})` : undefined}>{dur(r.predicted_s)}</span> },
    { key: "official_s", label: "Official", num: true, render: (r) => <>
      <b>{r.official ?? dur(r.official_s)}</b></> },
    { key: "pct", label: mode === "pct" ? "Index" : "vs predicted", num: true,
      title: mode === "pct" ? "(predicted − official) / predicted: + = faster than predicted" : "official − predicted: − = faster than predicted",
      sort: (r) => r.pct, render: (r) => <IdxCell pct={r.pct} delta={r.delta_s} mode={mode} /> },
  ];
  // others who did my ski races (columns), in /api/people order of appearance
  const others = [...new Set(ski.flatMap((r) => r.field.map((f) => f.person)))].filter((p) => p !== data.person);

  return (
    <main>
      <section class="first">
        {head(undefined,
          run.length ? <IdxToggle mode={mode} onMode={setMode} /> : null)}
        {!data.races.length ? <div class="empty-state"><p><b>No races yet.</b></p>
            <p class="muted">Official race results are kept in data/races.json; add a race there and rebuild Fitness.</p></div>
          : <>
            {!data.predictions && <p class="note">No fitness.json with predictions yet: rebuild Fitness to see the predicted times.</p>}
            {run.length > 0 && <div class="rx-tbl"><Table rows={run} rowKey={key} cols={cols} highlight={best} stickyCols={0} /></div>}
          </>}
      </section>
      {ski.length > 0 && (
        <section>
          <div class="head"><h2>Ski races</h2><span class="sub">results only, no prediction</span></div>
          <div class="rx-wrap">
            <table class="rx-plain">
              <thead><tr><th>Date</th><th>Race</th><th class="num">Distance</th>
                <th class="num pp sep" style={{ "--person": personColour(data.person) } as any}>{personName(data.person)}</th>
                {others.map((p) => <th key={p} class="num pp sep" style={{ "--person": personColour(p) } as any}>{personName(p)}</th>)}</tr></thead>
              <tbody>
                {ski.map((r) => {
                  const fastest = r.field[0]?.person;
                  return (
                    <tr key={key(r)}>
                      <td>{r.date}</td>
                      <td>{r.activity_id ? <a href={`/activities/${r.activity_id}`}>{r.name}</a> : r.name}</td>
                      <td class="num">{distLabel(r.km)}</td>
                      <td class={"num sep" + (fastest === data.person && r.field.length > 1 ? " lead" : "")}>{r.official ?? dur(r.official_s)}</td>
                      {others.map((p) => {
                        const f = r.field.find((x) => x.person === p);
                        return <td key={p} class={"num sep" + (fastest === p ? " lead" : "")}>{f ? <>{f.official ?? dur(f.official_s)}
                          <span class="gap" title={`vs ${personName(data.person)}`}>{gapCell(f.official_s - r.official_s, r.official_s, mode)}</span></> : <span class="muted">—</span>}</td>;
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {others.length > 0 && <p class="note">Bold = fastest of us; the small number is the gap to {personName(data.person)} ({mode === "pct" ? "% of " + personName(data.person) + "'s time" : "time"}; + = slower).</p>}
        </section>
      )}
    </main>
  );
}
