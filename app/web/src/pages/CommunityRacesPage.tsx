// Community · Races: head-to-head over every race in data/races.json (/api/community/races). Rows = races (newest
// first), columns = people. Run: official time + the index against each person's own pre-race prediction (green =
// faster than predicted). Ski: official time + gap to the fastest of us. % | time toggle shared with /races.
import { useEffect } from "preact/hooks";
import { useApi } from "../lib/api";
import { dur } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { distLabel, useIdxMode, type H2HDoc, type H2HRace, type IdxMode } from "../lib/races";
import { PageHeader } from "../components/PageHeader";
import { IdxCell, IdxToggle, gapCell } from "../components/RaceBits";
import "./community.css";
import "./races.css";

function Cell({ r, p, mode }: { r: H2HRace; p: string; mode: IdxMode }) {
  const x = r.results.find((y) => y.person === p);
  if (!x) return <td class="num sep muted">—</td>;
  const lead = r.results.length > 1 && r.results[0].person === p;
  const t = x.official ?? dur(x.official_s);
  return (
    <td class="num sep">
      <span class={lead ? "lead" : ""}>{t}</span>
      {r.sport === "run"
        ? <span style={{ marginLeft: "6px" }}><IdxCell pct={x.pct} delta={x.delta_s} mode={mode} /></span>
        : r.results.length > 1 && !lead && <span class="gap" title="behind the fastest of us">{gapCell(x.gap_s, r.results[0].official_s, mode)}</span>}
    </td>
  );
}

export function CommunityRacesPage() {
  const { data, error } = useApi<H2HDoc>("/api/community/races");
  const [mode, setMode] = useIdxMode();
  useEffect(() => { document.title = "Community · Races"; }, []);
  const head = (sub?: string) => (
    <PageHeader title="Races" sub={sub}
      about="Every official race, side by side: each runner against their own prediction, and skiers against each other."
      tips={[
        "Run: official time and the index against the runner's own prediction, made only from their data before race day. Green = faster than predicted (stronger = more), red = slower.",
        "Ski: official time and the gap to the fastest of us; ski races have no prediction (the ski model is too coarse for race times).",
        "% | time: the index as a percentage of the predicted time, or official − predicted (−0:34 = 34 s faster); ski gaps follow the same switch.",
        "Bold = fastest of us in that race. Results come from data/races.json.",
      ]}>{data && data.races.length ? <IdxToggle mode={mode} onMode={setMode} /> : null}</PageHeader>
  );
  if (error) return <main class="cm"><section class="first">{head()}<p class="err">Could not load races: {error}</p></section></main>;
  if (!data) return <main class="cm"><section class="first">{head()}<div class="empty">Loading…</div></section></main>;
  return (
    <main class="cm">
      <section class="first">
        {head()}
        {!data.races.length ? <div class="empty">No races yet (data/races.json).</div> : <>
          <div class="rx-wrap">
            <table class="rx-plain">
              <thead><tr><th>Date</th><th>Race</th><th class="num">Distance</th>
                {data.people.map((p) => <th key={p} class="num pp sep" style={{ "--person": personColour(p) } as any}>{personName(p)}</th>)}</tr></thead>
              <tbody>
                {data.races.map((r) => (
                  <tr key={r.date + r.name}>
                    <td>{r.date}</td>
                    <td>{r.name} {r.sport === "ski" && <span class="muted rx-src">ski</span>}</td>
                    <td class="num">{distLabel(r.km)}</td>
                    {data.people.map((p) => <Cell key={p} r={r} p={p} mode={mode} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>}
      </section>
    </main>
  );
}
