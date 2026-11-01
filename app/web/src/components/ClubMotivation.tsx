// Club page pieces (docs/community.md "Motivation"): the club goal as one line of text (editable, PATCH
// /api/community/goals/{id}) and "faster together than alone?" as one line per member with the details folded.
import { useEffect, useState } from "preact/hooks";
import { apiUrl } from "../lib/api";
import { isoLocal, shortDate } from "../lib/format";
import { personName } from "../lib/people";
import { delta, paceKm, plural, type CompanyShort, type Goal, type Goals } from "../lib/community";
import { PersonLink } from "./CommunityBits";

const fmtV = (v: number, unit: string) => (unit === "sessions" || unit === "reps" || unit === "m" || v >= 100 ? Math.round(v).toLocaleString("en-GB") : v.toFixed(1));

// ------------------------------------------------------------------ goal
async function send(method: string, url: string, body?: unknown): Promise<Goals> {
  const r = await fetch(apiUrl(url), { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `${r.status} ${r.statusText}`);
  return j as Goals;
}

const goalText = (g: Goal) =>
  `${g.title}: ${fmtV(g.value, g.unit)} of ${fmtV(g.target, g.unit)} ${g.unit} (${Math.round(g.pct * 100)} %)` +
  (g.status === "ended" ? " · ended" : g.status === "upcoming" ? ` · starts ${shortDate(g.from, "x")}` : g.status === "done" ? " · reached" : ` · ${plural(g.days_left, "day")} left`);

/** the club goal(s) as text lines; Edit opens an inline form (fetches its own, un-memoised data so an edit shows at once) */
export function GoalLine({ initial }: { initial?: Goal | null }) {
  const [data, setData] = useState<Goals | null>(null);
  const [edit, setEdit] = useState<Partial<Goal> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { let live = true; fetch(apiUrl("/api/community/goals")).then((r) => r.json()).then((j) => live && setData(j)).catch(() => {}); return () => { live = false; }; }, []);
  const g = data?.goal ?? initial ?? null;
  if (!g) return null;
  const all = [g, ...(data?.goals ?? []).filter((x) => x.id !== g.id)];
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
    <div class="cm-goal">
      {all.map((x) => (
        <div key={x.id}>
          <span title={`${shortDate(x.from, "x")} – ${shortDate(x.to, "x")}${x.default ? " · suggested goal" : ""}`}>Goal · {goalText(x)}</span>{" "}
          <button class="link" onClick={() => setEdit(edit?.id === x.id ? null : { ...x })}>{edit?.id === x.id ? "close" : "edit"}</button>
        </div>
      ))}
      {edit && (
        <form class="cm-goal-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <label>Title <input value={edit.title ?? ""} placeholder="e.g. 500 km together in 2026" onInput={(e) => setEdit({ ...edit, title: (e.target as HTMLInputElement).value })} /></label>
          <label>Target <input type="number" min="1" step="any" value={edit.target ?? ""} onInput={(e) => setEdit({ ...edit, target: Number((e.target as HTMLInputElement).value) })} /></label>
          <label>Of <select value={edit.metric} onChange={(e) => setEdit({ ...edit, metric: (e.target as HTMLSelectElement).value as Goal["metric"] })}>
            {metrics.map((m) => <option value={m.id}>{m.label}</option>)}</select></label>
          <label>From <input type="date" value={edit.from} onInput={(e) => setEdit({ ...edit, from: (e.target as HTMLInputElement).value })} /></label>
          <label>To <input type="date" value={edit.to} onInput={(e) => setEdit({ ...edit, to: (e.target as HTMLInputElement).value })} /></label>
          <button type="submit" disabled={busy}>{edit.id === "new" ? "Add goal" : "Save"}</button>
          {edit.id !== "new" && <button class="link" type="button" onClick={() => setEdit({ id: "new", title: `500 km together in ${y}`, metric: "km", target: 500, from: `${y}-01-01`, to: `${y}-12-31` })}>add another goal</button>}
          {edit.id !== "new" && edit.id !== "default" && <button class="link" type="button" onClick={del}>delete</button>}
          {err && <span class="err small">{err}</span>}
        </form>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ faster together than alone?
const verdict = (c: CompanyShort) => c.diff === null
  ? "not enough data yet"
  : c.text ?? (c.verdict === "company" ? `${delta(c.diff)} with company` : c.verdict === "alone" ? `${Math.abs(c.diff).toFixed(1)} better alone` : "the same together or alone");

/** one line per member ("Adrian · +1.2 with company · together +0.8 (6) · alone −0.4 (9)"), the rep-pace comparison behind a <details> */
export function CompanyLines({ rows }: { rows: { p: string; c: CompanyShort }[] }) {
  const pace = rows.filter((r) => r.c.pace.length);
  return (
    <div class="cm-company">
      <ul class="cm-lines">
        {rows.map(({ p, c }) => (
          <li key={p}>
            {(() => { const v = verdict(c), n = personName(p); return v.startsWith(n + " ") ? <><PersonLink p={p} />{v.slice(n.length)}</> : <><PersonLink p={p} />: {v}</>; })()}
            {c.diff !== null && <span class="muted"> · together {delta(c.joint_avg)} ({c.n_joint_cmp ?? c.n_joint}) · alone {delta(c.solo_avg)} ({c.n_solo_cmp ?? c.n_solo}){c.thin ? " · few sessions" : ""}</span>}
          </li>
        ))}
      </ul>
      <details>
        <summary>Details</summary>
        <p class="muted small">Form delta = a session's score minus the shape the day before (VDOT points). Together = club sessions; alone = the member's own
          sessions of the same kind within ±60 days. Rep pace compares interval sessions with reps of similar length (short &lt; 600 m, medium, long ≥ 1.4 km) within ±90 days.</p>
        {pace.length > 0 && (
          <table class="cm-tbl">
            <thead><tr><th>Member</th><th>Reps</th><th class="num">Together</th><th class="num">Alone</th><th>Difference</th><th class="num">Sessions</th></tr></thead>
            <tbody>
              {pace.flatMap(({ p, c }) => c.pace.map((x) => (
                <tr key={p + x.family}>
                  <td>{personName(p)}</td><td>{x.label}</td>
                  <td class="num">{paceKm(x.joint_s_km)}/km</td><td class="num">{paceKm(x.solo_s_km)}/km</td>
                  <td>{x.text}</td><td class="num">{x.n_joint} / {x.n_solo}</td>
                </tr>
              )))}
            </tbody>
          </table>
        )}
      </details>
    </div>
  );
}
