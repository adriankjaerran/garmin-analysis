// "Closest activities" for one activity (/api/nearest/{id}): top-k with their score (the component weights and the
// reasons are in tooltips). k and preset live in the URL (nk, np).
import { useEffect, useRef } from "preact/hooks";
import { useApi } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { dur, secs, speedUnit, speedVal } from "../lib/format";
import { compareUrl, type NearestResult, type NearRow } from "../lib/search";
import { Table } from "./Table";
import { DistCell, distSort, workoutText as appWorkoutText, workoutTip } from "./ActivityBits";

const PRESETS = [["balanced", "Balanced"], ["workout", "Same workout"], ["route", "Same route"], ["place", "Same place"]] as const;
const KS = [5, 10, 20];

type Row = NearRow & { place_name?: string | null; sport_label?: string | null; exact_label?: string | null; rest_group_s?: number | null };
/** the one workout name (store.workout_name) */
const workoutText = (r: Row) => r.workout_label ?? r.signature ?? r.family_label;
/** avg rep pace (total rep distance / total rep time) in the sport's unit, without the unit (it is in the header) */
const repPace = (r: Row) => speedVal(r.sport, r.rep_pace_ms ?? r.rep_speed_ms);
const restText = (r: Row) => (r.rest_s ? secs(r.rest_s) : "—");

export function NearestPanel({ id }: { id: string }) {
  const { query } = useLocation();
  const k = Number(query.get("nk")) || 10;
  const preset = query.get("np") || "balanced";
  const { data, error } = useApi<NearestResult>(`/api/nearest/${id}?k=${k}&preset=${preset}`);
  const sec = useRef<HTMLElement>(null);
  // /activities/<id>?go=closest (link from the Activities list): scroll here once the rows are in
  const go = query.get("go") === "closest";
  useEffect(() => { if (data && go) sec.current?.scrollIntoView({ block: "start" }); }, [data, go]);
  const rows = (data?.results ?? []) as Row[];
  const qr = data?.query as Row | undefined;
  const intervals = !!qr?.rep_count || rows.some((r) => r.rep_count);
  const ids = [id, ...rows.map((r) => r.id)];
  const comps = data ? Object.entries(data.weights).filter(([, w]) => w > 0).sort((a, b) => b[1] - a[1]) : [];
  const unit = speedUnit(qr?.sport);
  const self = qr ? [appWorkoutText(qr), qr.rest_s ? `rest ${restText(qr)}` : null, `${dur(qr.moving_s)} moving`].filter(Boolean).join(" · ") : "";

  return (
    <section id="closest" ref={sec}>
      <div class="head">
        <h2>Closest activities</h2>
        <span class="sub" title={data ? `Same sport, score 0–1; weights ${comps.map(([c, w]) => `${c} ${Math.round(w * 100)} %`).join(", ")}` : ""}>
          {data ? `This one: ${self}` : ""}</span>
        <div class="tools">
          <div class="seg">
            {PRESETS.map(([p, label]) => <button key={p} class={preset === p ? "on" : ""} onClick={() => setQuery({ np: p === "balanced" ? null : p })}>{label}</button>)}
          </div>
          <select value={String(k)} onChange={(e) => setQuery({ nk: (e.target as HTMLSelectElement).value === "10" ? null : (e.target as HTMLSelectElement).value })} title="How many">
            {KS.map((x) => <option key={x} value={x}>Top {x}</option>)}
          </select>
          <a class={"btn" + (rows.length ? "" : " disabled")} href={rows.length ? compareUrl(ids.slice(0, Math.min(ids.length, 6))) : undefined}
            title="This activity and the closest ones (up to 5) in Compare">Compare these</a>
        </div>
      </div>
      {error ? <div class="err">{error}</div> : (
        <Table rows={rows} rowKey={(r) => r.id} onRow={(r) => navigate(`/activities/${r.id}`)}
          defaultSort={["score", -1]} empty={data ? "No other activities of this sport." : "Loading…"} cols={[
            { key: "rank", label: "#", num: true, sort: (r) => -r.score, render: (r) => rows.indexOf(r) + 1 },
            { key: "date", label: "Date", render: (r) => <a href={`/activities/${r.id}`}>{r.date}</a> },
            { key: "workout", label: "Workout", sort: (r) => workoutText(r) ?? "", render: (r) => <span class="clip" style={{ maxWidth: "180px" }} title={workoutTip(r)}>{workoutText(r)}</span> },
            { key: "dist_km", label: "km", num: true, render: (r) => <DistCell a={r} />, sort: distSort },
            { key: "moving_s", label: "Moving", num: true, render: (r) => dur(r.moving_s), title: "Moving time" },
            ...(intervals ? [
              { key: "rep_pace_ms", label: `Rep ${unit}`, num: true, render: repPace, sort: (r: Row) => r.rep_pace_ms ?? r.rep_speed_ms ?? null, title: `Average rep pace (total rep distance / total rep time), ${unit}` },
              { key: "rest_s", label: "Rest", num: true, render: (r: Row) => <span title={r.rest_group_s ? `this session ${restText(r)}, its same-workout group ~${dur(r.rest_group_s)}` : ""}>{restText(r)}</span>,
                title: "Median rest between reps in that session (the ~ value in the workout label is its group's median)" },
            ] : []),
            { key: "place", label: "Place", render: (r) => { const p = r.place_name ?? r.place ?? "—"; return <span class="clip" style={{ maxWidth: "110px" }} title={p}>{p}</span>; }, sort: (r) => r.place_name ?? r.place ?? "" },
            { key: "score", label: "Score", num: true, title: "Similarity 0–1 (hover a score for the reasons)",
              render: (r) => <span title={r.reasons.map((x) => x.text).join("\n")}>{r.score.toFixed(2)}</span> },
          ]} />
      )}
    </section>
  );
}
