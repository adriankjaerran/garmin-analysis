// Community hero on top of a session page (/community/<gid>): the cover photo (else the tracks drawn on the
// category's gradient), the editable title, category select (six + automatic), date / time / place, kudos, Share and
// older / newer session navigation; the note (editable) right under it. Data: /api/community/sessions/{gid}
// via lib/communitySession.ts (shared with the members strip and the photo gallery).
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { longDate, shortDate } from "../lib/format";
import { personName } from "../lib/people";
import { patchSession, setKudos, someoneName, useCommunity, viewerOf, type CsSession, type Facts } from "../lib/communitySession";
import { ShareButton } from "./CommunitySessionShare";
import type { Replay, TSession } from "../lib/together";
import "../pages/community-session.css";

/** "2026-10-15_2012_Bislet" -> "Thu 15 Oct" (+ year when it differs from `ref`) */
const gidDate = (gid: string, ref?: string) => (/^\d{4}-\d\d-\d\d/.test(gid) ? shortDate(gid.slice(0, 10), ref) : gid);

/** everyone's track as SVG paths (white, over the gradient): a silhouette of where the session went */
function Tracks({ replay }: { replay: Replay }) {
  const paths = useMemo(() => {
    const ppl = Object.values(replay.people);
    const pts = ppl.map((p) => {
      const out: [number, number][] = [];
      const step = Math.max(1, Math.floor(p.t.length / 500));
      for (let i = 0; i < p.t.length; i += step) if (p.lat[i] !== null && p.lon[i] !== null) out.push([p.lon[i]!, p.lat[i]!]);
      return out;
    }).filter((v) => v.length > 1);
    if (!pts.length) return [];
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const v of pts) for (const [x, y] of v) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    const k = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
    const w = (x1 - x0) * k || 1e-6, h = y1 - y0 || 1e-6;
    // fit into the right part of a 1000 × 300 box (the text sits on the left)
    const W = 520, H = 250, s = Math.min(W / w, H / h);
    const ox = 1000 - 30 - (W + w * s) / 2, oy = 25 + (H - h * s) / 2;
    return pts.map((v) => v.map(([x, y], i) => `${i ? "L" : "M"}${(ox + (x - x0) * k * s).toFixed(1)} ${(oy + (y1 - y) * s).toFixed(1)}`).join(""));
  }, [replay]);
  if (!paths.length) return null;
  return (
    <svg class="cs-tracks" viewBox="0 0 1000 300" preserveAspectRatio="xMaxYMid meet" aria-hidden="true">
      {paths.map((d, i) => <path key={i} d={d} />)}
    </svg>
  );
}

function TitleEdit({ c }: { c: CsSession }) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (edit) { ref.current?.focus(); ref.current?.select(); } }, [edit]);
  const save = async () => {
    const t = v.trim();
    if (t === (c.meta.title ?? "")) { setEdit(false); return; }
    setBusy(true); setErr(null);
    try { await patchSession(c.gid, { title: t || null }); setEdit(false); } catch (e: any) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  const title = c.meta.title || c.display_title || "Session";
  if (!edit) {
    return (
      <h1 class="cs-title">
        <button class="cs-title-btn" onClick={() => { setV(c.meta.title ?? ""); setEdit(true); }} title={c.meta.title ? "Rename this session" : "Give this session a name (e.g. \"Windy track night\")"}>
          <span>{title}</span><i class="cs-pen" aria-hidden="true">✎</i>
        </button>
      </h1>
    );
  }
  return (
    <div class="cs-title-edit">
      <input ref={ref} value={v} maxLength={120} placeholder={c.display_title || "Name this session"} aria-label="Session title" disabled={busy}
        onInput={(e) => setV((e.target as HTMLInputElement).value)}
        onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEdit(false); }} onBlur={save} />
      <span class="cs-hint">{err ? <span class="cs-err">{err}</span> : "Enter saves · Esc cancels · empty = the workout name"}</span>
    </div>
  );
}

function CategorySelect({ c }: { c: CsSession }) {
  const [busy, setBusy] = useState(false);
  const auto = c.categories.find((x) => x.id === c.meta.category_auto);
  const val = c.meta.category_manual ? c.meta.category ?? "auto" : "auto";
  return (
    <label class="cs-cat" title={`Category${c.meta.category_reason ? ` · automatic: ${auto?.label ?? c.meta.category_auto} (${c.meta.category_reason})` : ""}`}>
      <span class="cs-cat-emoji" aria-hidden="true">{c.category_info.emoji}</span>
      <select value={val} disabled={busy} aria-label="Category" onChange={async (e) => {
        setBusy(true);
        try { await patchSession(c.gid, { category: (e.target as HTMLSelectElement).value }); } finally { setBusy(false); }
      }}>
        <option value="auto">{`auto (${auto ? auto.label : c.meta.category_auto})`}</option>
        {c.categories.map((x) => <option key={x.id} value={x.id}>{`${x.emoji} ${x.label}`}</option>)}
      </select>
    </label>
  );
}

function Kudos({ c }: { c: CsSession }) {
  const me = viewerOf(c);
  const names = c.meta.kudos;
  const on = c.kudoed ?? names.includes(me);
  const [busy, setBusy] = useState(false);
  const [pop, setPop] = useState(false);
  const toggle = async () => {
    setBusy(true);
    try { await setKudos(c.gid, me, !on); if (!on) { setPop(true); setTimeout(() => setPop(false), 700); } } finally { setBusy(false); }
  };
  return (
    <div class="cs-kudos">
      <button class={"cs-kudos-btn" + (on ? " on" : "") + (pop ? " pop" : "")} aria-pressed={on} disabled={busy} onClick={toggle}
        title={on ? `You (${personName(me)}) gave kudos · click to take it back` : `Give kudos as ${personName(me)}`}>
        <span aria-hidden="true">👏</span>{on ? "Kudos given" : "Give kudos"}{names.length ? <b>{names.length}</b> : null}
      </button>
      {names.length > 0 && <span class="cs-kudos-names">👏 {names.map((p) => someoneName(c, p, personName)).join(", ")}</span>}
    </div>
  );
}

function Note({ c }: { c: CsSession }) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (edit) ref.current?.focus(); }, [edit]);
  const note = c.meta.note;
  const save = async () => {
    setBusy(true); setErr(null);
    try { await patchSession(c.gid, { note: v.trim() || null }); setEdit(false); } catch (e: any) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  if (edit) {
    return (
      <div class="cs-note editing">
        <textarea ref={ref} value={v} rows={Math.min(10, Math.max(3, v.split("\n").length + 1))} maxLength={4000} disabled={busy}
          placeholder="How did it go? Weather, the track, who pulled, the coffee afterwards…" aria-label="Note"
          onInput={(e) => setV((e.target as HTMLTextAreaElement).value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") setEdit(false); }} />
        <div class="cs-note-tools">
          <button class="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save note"}</button>
          <button onClick={() => setEdit(false)} disabled={busy}>Cancel</button>
          <span class="cs-hint">{err ? <span class="cs-err">{err}</span> : "⌘/Ctrl + Enter saves"}</span>
        </div>
      </div>
    );
  }
  if (!note) return <div class="cs-note empty"><button class="link" onClick={() => { setV(""); setEdit(true); }}>＋ Add a note</button><span class="muted"> — the story of the session, for the club</span></div>;
  return (
    <div class="cs-note">
      <p>{note}</p>
      <button class="link cs-note-edit" onClick={() => { setV(note); setEdit(true); }} title="Edit the note">✎ Edit</button>
    </div>
  );
}

export function CommunitySessionHero({ gid, s, replay, facts }: { gid: string; s: TSession; replay: Replay | null; facts: Facts }) {
  const { data: c, error } = useCommunity(gid);
  const base = "/community";
  if (!c) return error ? <div class="cs-hero-missing note small">Club details unavailable: {error}</div> : <div class="cs-hero cs-hero-loading" />;
  const m = c.meta;
  const cover = m.photos.find((p) => p.id === m.cover) ?? null;
  const cat = c.category_info.id;
  const place = c.place?.name ?? s.group.place?.name;
  const nT = c.n_timed, nG = c.n_guests;
  return (
    <>
      <section class={`first cs-hero cat-${cat}` + (cover ? " photo" : "")}>
        <div class="cs-hero-bg" aria-hidden="true">
          {cover ? <img src={cover.url} alt="" /> : <>{replay && <Tracks replay={replay} />}<span class="cs-hero-emoji">{c.category_info.emoji}</span></>}
        </div>
        <div class="cs-hero-top">
          <a class="cs-back" href={base}>‹ Sessions</a>
          <span class="cs-nav">
            {c.nav.older ? <a href={`${base}/${encodeURIComponent(c.nav.older)}`} title={`Older session: ${c.nav.older}`}>‹ {gidDate(c.nav.older, c.date)}</a> : <span class="off">‹ older</span>}
            {c.nav.newer ? <a href={`${base}/${encodeURIComponent(c.nav.newer)}`} title={`Newer session: ${c.nav.newer}`}>{gidDate(c.nav.newer, c.date)} ›</a> : <span class="off">newer ›</span>}
          </span>
        </div>
        <div class="cs-hero-body">
          <CategorySelect c={c} />
          <TitleEdit c={c} />
          <div class="cs-when">
            <span>{longDate(c.date)}</span><span>{c.start_local}</span>
            {place && <span title={c.place?.name_local ?? undefined}>📍 {place}</span>}
            <span>{nT} member{nT === 1 ? "" : "s"}{nG ? ` + ${nG} guest${nG === 1 ? "" : "s"}` : ""}</span>
            {m.title && c.display_title !== m.title && c.workout && <span class="cs-when-wk">{c.workout}</span>}
          </div>
        </div>
        <div class="cs-hero-foot">
          <Kudos c={c} />
          <ShareButton s={s} c={c} facts={facts} replay={replay} />
          {m.photos.length > 0 && <a class="cs-photos-link" href="#cs-photos" onClick={(e) => { e.preventDefault(); document.getElementById("cs-photos")?.scrollIntoView({ behavior: "smooth" }); }}>📷 {m.photos.length} photo{m.photos.length === 1 ? "" : "s"}</a>}
        </div>
      </section>
      <Note c={c} />
      {c.store_error && <p class="note small cs-err">⚠ data/community.json could not be read ({c.store_error}); edits are kept in a fresh file.</p>}
    </>
  );
}
