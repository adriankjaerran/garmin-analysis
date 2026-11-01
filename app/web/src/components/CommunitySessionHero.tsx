// Top of a session page (/community/<gid>): a plain title line ("15×400 · Tue 20 Oct 20:07 · Bislett"), small text
// links (kudos, share, compare, older / newer) and an Edit menu (title, type, guests, note); the note under it.
// Data: /api/community/sessions/{gid} via lib/communitySession.ts (shared with the members table and the photos).
import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { shortDate } from "../lib/format";
import { personName } from "../lib/people";
import { patchSession, setKudos, someoneName, useCommunity, viewerOf, type CsSession, type Facts } from "../lib/communitySession";
import { ShareButton } from "./CommunitySessionShare";
import type { Replay, TSession } from "../lib/together";
import "../pages/community-session.css";

/** "2026-10-15_2012_Bislet" -> "Thu 15 Oct" (+ year when it differs from `ref`) */
const gidDate = (gid: string, ref?: string) => (/^\d{4}-\d\d-\d\d/.test(gid) ? shortDate(gid.slice(0, 10), ref) : gid);

function KudosLink({ c }: { c: CsSession }) {
  const me = viewerOf(c);
  const names = c.meta.kudos;
  const on = c.kudoed ?? names.includes(me);
  const [busy, setBusy] = useState(false);
  const who = names.length ? `Kudos from ${names.map((p) => someoneName(c, p, personName)).join(", ")}` : "No kudos yet";
  return (
    <button class="link" aria-pressed={on} disabled={busy} title={`${who} · ${on ? `click to take back ${personName(me)}'s kudos` : `give kudos as ${personName(me)}`}`}
      onClick={async () => { setBusy(true); try { await setKudos(c.gid, me, !on); } finally { setBusy(false); } }}>
      {on ? "Kudos given" : "Kudos"}{names.length ? ` (${names.length})` : ""}
    </button>
  );
}

/** the Edit menu: title, type override, guests, note */
function EditMenu({ c }: { c: CsSession }) {
  const m = c.meta;
  const [title, setTitle] = useState(m.title ?? "");
  const [note, setNote] = useState(m.note ?? "");
  const [guest, setGuest] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setTitle(m.title ?? ""); setNote(m.note ?? ""); }, [m.title, m.note]);
  const save = async (body: Parameters<typeof patchSession>[1]) => {
    setBusy(true); setErr(null);
    try { await patchSession(c.gid, body); } catch (e: any) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  const auto = c.categories.find((x) => x.id === m.category_auto);
  const addGuests = async () => {
    const names = guest.split(",").map((x) => x.trim()).filter(Boolean);
    if (names.length) { await save({ guests: [...m.guests, ...names] }); setGuest(""); }
  };
  return (
    <details class="cs-edit">
      <summary>Edit</summary>
      <div class="cs-edit-box">
        <label>Title
          <input value={title} maxLength={120} placeholder={c.display_title || "Name this session"} disabled={busy}
            onInput={(e) => setTitle((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === "Enter" && save({ title: title.trim() || null })} />
          <button disabled={busy || title.trim() === (m.title ?? "")} onClick={() => save({ title: title.trim() || null })}>Save</button>
        </label>
        <label title={m.category_reason ? `automatic: ${auto?.label ?? m.category_auto} (${m.category_reason})` : undefined}>Type
          <select value={m.category_manual ? m.category ?? "auto" : "auto"} disabled={busy} onChange={(e) => save({ category: (e.target as HTMLSelectElement).value })}>
            <option value="auto">{`automatic (${auto ? auto.label : m.category_auto})`}</option>
            {c.categories.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </select>
        </label>
        <div class="cs-edit-row"><span>Guests</span>
          {m.guests.map((name) => (
            <span key={name} class="cs-guest">{name} <button class="link" disabled={busy} title={`Remove ${name}`} onClick={() => save({ guests: m.guests.filter((x) => x !== name) })}>remove</button></span>
          ))}
          <input value={guest} placeholder="+ guest (names, comma separated)" maxLength={200} disabled={busy}
            onInput={(e) => setGuest((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === "Enter" && addGuests()} />
          <button disabled={busy || !guest.trim()} onClick={addGuests}>Add</button>
        </div>
        <label class="cs-edit-note">Note
          <textarea value={note} rows={3} maxLength={4000} disabled={busy} placeholder="How did it go?"
            onInput={(e) => setNote((e.target as HTMLTextAreaElement).value)} />
          <button disabled={busy || note.trim() === (m.note ?? "")} onClick={() => save({ note: note.trim() || null })}>Save</button>
        </label>
        {err && <span class="cs-err small">{err}</span>}
      </div>
    </details>
  );
}

export function CommunitySessionHero({ gid, s, replay, facts, links }: { gid: string; s: TSession; replay: Replay | null; facts: Facts; links?: ComponentChildren }) {
  const { data: c, error } = useCommunity(gid);
  const base = "/community";
  const title = c ? c.meta.title || c.display_title || s.group.workout : s.group.workout;
  const date = c?.date ?? s.group.date, start = c?.start_local ?? s.group.start_local;
  const place = c?.place?.name ?? s.group.place?.name;
  return (
    <header class="cs-head">
      <a class="cs-back" href={base}>‹ Sessions</a>
      <h1 class="cs-title">{[title, `${shortDate(date)} ${start}`, place].filter(Boolean).join(" · ")}</h1>
      {c && <div class="cs-links">
        {c.meta.title && c.display_title !== c.meta.title && c.workout && <span class="muted">{c.workout}</span>}
        <span class="muted">{c.category_info.label}</span>
        <KudosLink c={c} />
        <ShareButton s={s} c={c} facts={facts} replay={replay} />
        {links}
        {c.nav.older && <a href={`${base}/${encodeURIComponent(c.nav.older)}`} title={`Older session: ${c.nav.older}`}>‹ {gidDate(c.nav.older, c.date)}</a>}
        {c.nav.newer && <a href={`${base}/${encodeURIComponent(c.nav.newer)}`} title={`Newer session: ${c.nav.newer}`}>{gidDate(c.nav.newer, c.date)} ›</a>}
        <EditMenu c={c} />
      </div>}
      {error && <p class="note small">Club details unavailable: {error}</p>}
      {c?.meta.note && <p class="cs-note">{c.meta.note}</p>}
      {facts.story.length > 0 && <p class="cs-story">{facts.story.join(" ")}</p>}
      {c?.store_error && <p class="note small cs-err">data/community.json could not be read ({c.store_error}); edits are kept in a fresh file.</p>}
    </header>
  );
}
