// "Closest activities" for one activity (/api/nearest/{id}): top-k with score, component bars and reasons,
// plus a mini map overlaying their tracks on the query track. k and preset live in the URL (nk, np).
import { useMemo, useState } from "preact/hooks";
import { useApi } from "../lib/api";
import { navigate, setQuery, useLocation } from "../lib/router";
import { dur, km } from "../lib/format";
import { decodePolyline, unionBBox, type BBox } from "../lib/geo";
import { compareUrl, type NearestResult, type NearRow } from "../lib/search";
import { Table } from "./Table";
import { TracksMap } from "./TracksMap";
import { SportDot } from "./ActivityBits";

const PRESETS = [["balanced", "Balanced"], ["workout", "Same workout"], ["route", "Same route"], ["place", "Same place"]] as const;
const KS = [5, 10, 20];

const workoutText = (r: NearRow) => (r.rep_count ? (r.exact_key ?? r.signature) : (r.signature ?? r.family_label));

function Components({ r, weights }: { r: NearRow; weights: Record<string, number> }) {
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
  const rows = data?.results ?? [];
  const tracks = useMemo(() => rows.filter((r) => r.poly).map((r) => ({ id: r.id, sport: r.sport, poly: r.poly, width: 2.5 })), [data]);
  // this activity: dashed black on top (hovering a row draws that one solid black)
  const extra = useMemo<GeoJSON.FeatureCollection | undefined>(() => data?.query.poly ? { type: "FeatureCollection", features: [
    { type: "Feature", properties: { color: "#1d2125", width: 3 }, geometry: { type: "LineString", coordinates: decodePolyline(data.query.poly) } }] } : undefined, [data]);
  const fit = useMemo(() => unionBBox([data?.query.bbox, ...rows.filter((r) => r.poly).map((r) => r.bbox)].filter((b): b is BBox => !!b)), [data]);
  const ids = [id, ...rows.map((r) => r.id)];
  const comps = data ? Object.entries(data.weights).filter(([, w]) => w > 0).sort((a, b) => b[1] - a[1]) : [];

  return (
    <section>
      <div class="head">
        <h2>Closest activities</h2>
        <span class="sub">{data ? `Same sport, score 0–1; weights ${comps.map(([c, w]) => `${c} ${Math.round(w * 100)} %`).join(", ")}` : ""}</span>
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
        <div class="cols" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(280px, 2fr)" }}>
          <Table rows={rows} rowKey={(r) => r.id} highlight={hover} onHover={(r) => setHover(r?.id ?? null)} onRow={(r) => navigate(`/activities/${r.id}`)}
            defaultSort={["score", -1]} empty={data ? "No other activities of this sport." : "Loading…"} cols={[
              { key: "rank", label: "#", num: true, sort: (r) => -r.score, render: (r) => rows.indexOf(r) + 1 },
              { key: "date", label: "Date", render: (r) => <a href={`/activities/${r.id}`}>{r.date}</a> },
              { key: "workout", label: "Workout", sort: (r) => workoutText(r) ?? "", render: (r) => <span title={r.name ?? ""}><SportDot sport={r.sport} />{workoutText(r)}</span> },
              { key: "dist_km", label: "km", num: true, render: (r) => km(r.dist_km) },
              { key: "place", label: "Place", render: (r) => r.place ?? "—" },
              { key: "score", label: "Score", num: true, render: (r) => r.score.toFixed(2) },
              { key: "parts", label: "Components", sort: (r) => r.score, title: "One bar per weighted component (width = weight, fill = score; faint = not computable, counts 0.4). Hover for values.",
                render: (r) => <Components r={r} weights={data!.weights} /> },
              { key: "reasons", label: "Why", sort: (r) => r.reasons[0]?.text ?? "", render: (r) => <span class="muted why" title={"Moving " + dur(r.moving_s) + "\n" + r.reasons.map((x) => `${x.text} (${x.c} ${x.s?.toFixed(2)})`).join("\n")}>{r.reasons.slice(0, 2).map((x, i) => <div key={i}>{x.text}</div>)}</span> },
            ]} />
          <TracksMap tracks={tracks} extra={extra} fit={fit} highlight={hover} onHover={setHover} onClick={(x) => x !== id && navigate(`/activities/${x}`)}
            style={{ height: "360px" }} class="bordered" markers={false} compact empty={data ? (extra ? "" : "No GPS tracks") : "Loading…"}>
            {extra && <div class="overlay bl">dashed = this activity</div>}
          </TracksMap>
        </div>
      )}
    </section>
  );
}
