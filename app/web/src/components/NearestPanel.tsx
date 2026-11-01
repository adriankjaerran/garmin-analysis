// "Closest activities" for one activity (/api/nearest/{id}): top-k with score, component bars and reasons,
// plus a mini map overlaying their tracks on the query track. k and preset live in the URL (nk, np).
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useApi } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { dur, km, secs, speedUnit, speedVal } from "../lib/format";
import { decodePolyline, unionBBox, type BBox } from "../lib/geo";
import { compareUrl, coreBBox, type NearestResult, type NearRow } from "../lib/search";
import { Table } from "./Table";
import { TracksMap } from "./TracksMap";
import { SportDot, TogetherMark, workoutText as appWorkoutText, workoutTip } from "./ActivityBits";

const PRESETS = [["balanced", "Balanced"], ["workout", "Same workout"], ["route", "Same route"], ["place", "Same place"]] as const;
const KS = [5, 10, 20];

type Row = NearRow & { place_name?: string | null; sport_label?: string | null; exact_label?: string | null; rest_group_s?: number | null };
/** the one workout name (store.workout_name) */
const workoutText = (r: Row) => r.workout_label ?? r.signature ?? r.family_label;
/** avg rep pace (total rep distance / total rep time) in the sport's unit, without the unit (it is in the header) */
const repPace = (r: Row) => speedVal(r.sport, r.rep_pace_ms ?? r.rep_speed_ms);
const restText = (r: Row) => (r.rest_s ? secs(r.rest_s) : "—");

function Components({ r, weights }: { r: Row; weights: Record<string, number> }) {
  const cs = Object.entries(weights).filter(([, w]) => w > 0).sort((a, b) => b[1] - a[1]);
  return (
    <span class="cbars" title={cs.map(([c]) => `${c} ${r.components[c] !== undefined ? r.components[c].toFixed(2) : "n/a (0.40)"}`).join("\n")}>
      {cs.map(([c, w]) => {
        const v = r.components[c];
        return <i key={c} style={{ width: `${Math.max(3, Math.round(w * 60))}px` }}><b style={{ width: `${Math.round((v ?? 0.4) * 100)}%`, opacity: v === undefined ? 0.35 : 1 }} /></i>;
      })}
    </span>
  );
}

export function NearestPanel({ id }: { id: string }) {
  const { query } = useLocation();
  const k = Number(query.get("nk")) || 10;
  const preset = query.get("np") || "balanced";
  const { data, error } = useApi<NearestResult>(`/api/nearest/${id}?k=${k}&preset=${preset}`);
  const [hover, setHover] = useState<string | null>(null);
  const sec = useRef<HTMLElement>(null);
  // /activities/<id>?go=closest (link from the Activities list): scroll here once the rows are in
  const go = query.get("go") === "closest";
  useEffect(() => { if (data && go) sec.current?.scrollIntoView({ block: "start" }); }, [data, go]);
  const rows = (data?.results ?? []) as Row[];
  const qr = data?.query as Row | undefined;
  const intervals = !!qr?.rep_count || rows.some((r) => r.rep_count);
  const tracks = useMemo(() => rows.filter((r) => r.poly).map((r) => ({ id: r.id, sport: r.sport, poly: r.poly, width: 2.5 })), [data]);
  // this activity: dashed black on top (hovering a row draws that one solid black)
  const extra = useMemo<GeoJSON.FeatureCollection | undefined>(() => data?.query.poly ? { type: "FeatureCollection", features: [
    { type: "Feature", properties: { color: "#1d2125", width: 3 }, geometry: { type: "LineString", coordinates: decodePolyline(data.query.poly) } }] } : undefined, [data]);
  // fit: this track + the closest ones near it (≤ ~50 km), so a hike abroad doesn't zoom out to the world
  const fit = useMemo(() => {
    const q = data?.query.bbox;
    const near = (b: BBox) => !q || (Math.abs((b[0] + b[2]) / 2 - (q[0] + q[2]) / 2) < 1 && Math.abs((b[1] + b[3]) / 2 - (q[1] + q[3]) / 2) < 0.5);
    const bs = rows.filter((r) => r.poly && r.bbox).map((r) => r.bbox as BBox);
    const close = bs.filter(near);
    // no track of its own (treadmill, pool): the densest cluster of the results, not every one of them
    if (!q) return (coreBBox(bs).fit as BBox | null) ?? unionBBox(bs);
    return unionBBox([q, ...close]);
  }, [data]);
  const ids = [id, ...rows.map((r) => r.id)];
  const comps = data ? Object.entries(data.weights).filter(([, w]) => w > 0).sort((a, b) => b[1] - a[1]) : [];
  const hasMap = !data || !!extra || tracks.length > 0;  // hide the map when nothing has a track (pool swims, treadmill)
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
        <div class="cols" style={{ gridTemplateColumns: hasMap ? "minmax(0, 3fr) minmax(260px, 1.3fr)" : "minmax(0, 1fr)" }}>
          <Table rows={rows} rowKey={(r) => r.id} highlight={hover} onHover={(r) => setHover(r?.id ?? null)} onRow={(r) => navigate(`/activities/${r.id}`)}
            defaultSort={["score", -1]} empty={data ? "No other activities of this sport." : "Loading…"} cols={[
              { key: "rank", label: "#", num: true, sort: (r) => -r.score, render: (r) => rows.indexOf(r) + 1 },
              { key: "date", label: "Date", render: (r) => <a href={`/activities/${r.id}`}>{r.date}</a> },
              { key: "workout", label: "Workout", sort: (r) => workoutText(r) ?? "", render: (r) => <span class="clip" style={{ maxWidth: "180px" }} title={workoutTip(r)}><SportDot sport={r.sport} /><TogetherMark a={r} />{workoutText(r)}</span> },
              { key: "dist_km", label: "km", num: true, render: (r) => km(r.dist_km) },
              { key: "moving_s", label: "Moving", num: true, render: (r) => dur(r.moving_s), title: "Moving time" },
              ...(intervals ? [
                { key: "rep_pace_ms", label: `Rep ${unit}`, num: true, render: repPace, sort: (r: Row) => r.rep_pace_ms ?? r.rep_speed_ms ?? null, title: `Average rep pace (total rep distance / total rep time), ${unit}` },
                { key: "rest_s", label: "Rest", num: true, render: (r: Row) => <span title={r.rest_group_s ? `this session ${restText(r)}, its same-workout group ~${dur(r.rest_group_s)}` : ""}>{restText(r)}</span>,
                  title: "Median rest between reps in that session (the ~ value in the workout label is its group's median)" },
              ] : []),
              { key: "place", label: "Place", render: (r) => { const p = r.place_name ?? r.place ?? "—"; return <span class="clip" style={{ maxWidth: "110px" }} title={p}>{p}</span>; }, sort: (r) => r.place_name ?? r.place ?? "" },
              { key: "score", label: "Score", num: true, render: (r) => r.score.toFixed(2) },
              { key: "parts", label: "Why", sort: (r) => r.score, title: "One bar per weighted component (width = weight, fill = how close; faint = not computable). Hover for the reasons.",
                render: (r) => <span class="why-cell" title={r.reasons.map((x) => x.text).join("\n")}><Components r={r} weights={data!.weights} /><span class="why-i" aria-label="why">ⓘ</span></span> },
            ]} />
          {hasMap && <TracksMap tracks={tracks} extra={extra} fit={fit} highlight={hover} onHover={setHover} onClick={(x) => x !== id && navigate(`/activities/${x}`)}
            style={{ height: "360px" }} class="bordered" markers={false} compact empty={data ? (extra ? "" : "No GPS tracks") : "Loading…"}>
            {extra && <div class="overlay bl">dashed = this activity</div>}
          </TracksMap>}
        </div>
      )}
    </section>
  );
}
