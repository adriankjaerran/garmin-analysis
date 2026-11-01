// Photos of a club session: a gallery grid (thumbnails, the cover marked), a lightbox (← / → / Esc), upload by
// drag & drop onto the section or the file picker (several at once, a progress bar each), delete and "set as cover".
// Photos stay local: data/_community/photos/<gid>/ (≤ 1600 px jpg + 480 px thumbnail; docs/community.md).
import { useEffect, useRef, useState } from "preact/hooks";
import { personName } from "../lib/people";
import { isoLocal } from "../lib/format";
import { deletePhoto, patchSession, reload, someoneName, uploadPhoto, useCommunity, type CsPhoto, type CsSession } from "../lib/communitySession";
import "../pages/community-session.css";

type Up = { id: number; name: string; f: number; err: string | null; done: boolean };
let upSeq = 0;
const OK = /\.(jpe?g|png|webp|heic|heif)$/i;

/** EXIF time as recorded (local, naive); the upload time (UTC) in local time */
const when = (p: CsPhoto) => {
  if (p.taken) return p.taken.replace("T", " ").slice(0, 16);
  if (!p.at) return null;
  const d = new Date(p.at);
  return isNaN(d.getTime()) ? null : `${isoLocal(d)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

function Lightbox({ c, i, setI }: { c: CsSession; i: number; setI: (i: number | null) => void }) {
  const ps = c.meta.photos;
  const p = ps[i];
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") setI(null);
      else if (e.key === "ArrowLeft" && i > 0) setI(i - 1);
      else if (e.key === "ArrowRight" && i < ps.length - 1) setI(i + 1);
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, [i, ps.length]);
  if (!p) return null;
  const cover = c.meta.cover === p.id;
  const del = async () => {
    if (!confirm("Delete this photo? It is removed from this computer.")) return;
    setBusy(true);
    try { await deletePhoto(c.gid, p.id); setI(ps.length <= 1 ? null : Math.min(i, ps.length - 2)); } finally { setBusy(false); }
  };
  return (
    <div class="cs-lb" role="dialog" aria-label="Photo" onClick={(e) => e.target === e.currentTarget && setI(null)}>
      <button class="cs-lb-x" onClick={() => setI(null)} aria-label="Close" title="Close (Esc)">✕</button>
      {i > 0 && <button class="cs-lb-nav prev" onClick={() => setI(i - 1)} aria-label="Previous photo" title="Previous (←)">‹</button>}
      <img src={p.url} alt={p.name ?? "photo"} onClick={(e) => e.stopPropagation()} />
      {i < ps.length - 1 && <button class="cs-lb-nav next" onClick={() => setI(i + 1)} aria-label="Next photo" title="Next (→)">›</button>}
      <div class="cs-lb-bar" onClick={(e) => e.stopPropagation()}>
        <span>{i + 1} / {ps.length}{p.by ? ` · by ${someoneName(c, p.by, personName)}` : ""}{when(p) ? ` · ${p.taken ? "taken" : "added"} ${when(p)}` : ""}</span>
        <span class="cs-lb-tools">
          <button disabled={busy || cover} onClick={async () => { setBusy(true); try { await patchSession(c.gid, { cover: p.id }); } finally { setBusy(false); } }}>
            {cover ? "★ Cover" : "☆ Set as cover"}</button>
          <a class="btn" href={p.url} target="_blank" rel="noopener">Open ↗</a>
          <button class="danger" disabled={busy} onClick={del}>Delete</button>
        </span>
      </div>
    </div>
  );
}

export function CommunitySessionPhotos({ gid }: { gid: string }) {
  const { data: c } = useCommunity(gid);
  const [ups, setUps] = useState<Up[]>([]);
  const [drag, setDrag] = useState(0);
  const [lb, setLb] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { setLb(null); setUps([]); }, [gid]);
  if (!c) return null;
  const ps = c.meta.photos;
  const files = async (list: FileList | File[] | null) => {
    const fs = [...(list ?? [])];
    if (!fs.length) return;
    const mine: Up[] = fs.map((f) => ({ id: ++upSeq, name: f.name, f: 0, err: f.type.startsWith("image/") || OK.test(f.name) ? null : "not an image", done: false }));
    setUps((u) => [...u.filter((x) => !x.done || x.err), ...mine]);
    const set = (id: number, patch: Partial<Up>) => setUps((u) => u.map((x) => (x.id === id ? { ...x, ...patch } : x)));
    for (let j = 0; j < fs.length; j++) {
      if (mine[j].err) continue;
      try {
        await uploadPhoto(gid, fs[j], (f) => set(mine[j].id, { f }));
        set(mine[j].id, { f: 1, done: true });
      } catch (e: any) { set(mine[j].id, { err: String(e.message || e) }); }
    }
    await reload(gid);
    setTimeout(() => setUps((u) => u.filter((x) => !(x.done && !x.err) || !mine.some((m) => m.id === x.id))), 1500);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(0); files(e.dataTransfer?.files ?? null); };
  const dragOn = (e: DragEvent) => { if ([...(e.dataTransfer?.types ?? [])].includes("Files")) { e.preventDefault(); } };
  return (
    <section id="cs-photos" class={"cs-photos" + (drag ? " drag" : "")}
      onDragEnter={(e) => { dragOn(e); if ([...(e.dataTransfer?.types ?? [])].includes("Files")) setDrag((d) => d + 1); }}
      onDragOver={dragOn} onDragLeave={() => setDrag((d) => Math.max(0, d - 1))} onDrop={onDrop}>
      <div class="head"><h2>Photos</h2>
        <span class="sub">{ps.length ? `${ps.length} photo${ps.length === 1 ? "" : "s"} · click to enlarge · ★ = the cover on top` : "the session in pictures"}</span>
        <div class="tools">
          <button onClick={() => input.current?.click()} title="Choose photos (jpg, png, webp, heic; ≤ 15 MB each)">＋ Add photos</button>
          <input ref={input} type="file" accept="image/*,.heic,.heif" multiple hidden onChange={(e) => { const t = e.target as HTMLInputElement; files(t.files); t.value = ""; }} />
        </div>
      </div>
      {ps.length > 0 && (
        <div class="cs-grid-ph">
          {ps.map((p, i) => (
            <figure key={p.id} class={c.meta.cover === p.id ? "cover" : ""}>
              <button class="cs-ph" onClick={() => setLb(i)} title={p.name ?? "photo"} aria-label={`Open photo ${i + 1}`}>
                <img src={p.thumb} alt={p.name ?? `photo ${i + 1}`} loading="lazy" />
              </button>
              {c.meta.cover === p.id && <span class="cs-cover-tag" title="The cover photo on top of the page">★ cover</span>}
              <span class="cs-ph-tools">
                {c.meta.cover !== p.id && <button title="Set as cover" aria-label="Set as cover" onClick={() => patchSession(gid, { cover: p.id })}>☆</button>}
                <button title="Delete" aria-label="Delete photo" onClick={() => confirm("Delete this photo? It is removed from this computer.") && deletePhoto(gid, p.id)}>🗑</button>
              </span>
            </figure>
          ))}
        </div>
      )}
      <div class={"cs-drop" + (ps.length ? " slim" : "")} onClick={() => input.current?.click()} role="button" tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}>
        <span class="cs-drop-ic" aria-hidden="true">📷</span>
        <span><b>{drag ? "Drop to upload" : "Drop photos here"}</b> or click to choose{ps.length ? "" : " — group selfies, the track, the coffee afterwards"}</span>
        <span class="muted small">jpg · png · webp · heic, ≤ 15 MB · stored on this computer only</span>
      </div>
      {ups.length > 0 && (
        <ul class="cs-ups">
          {ups.map((u) => (
            <li key={u.id} class={u.err ? "err" : u.done ? "done" : ""}>
              <span class="nm">{u.name}</span>
              <span class="bar"><i style={{ width: `${Math.round(u.f * 100)}%` }} /></span>
              <span class="st">{u.err ? u.err : u.done ? "✓" : `${Math.round(u.f * 100)} %`}</span>
              {(u.err || u.done) && <button class="cs-x" aria-label="Dismiss" onClick={() => setUps((x) => x.filter((y) => y.id !== u.id))}>×</button>}
            </li>
          ))}
        </ul>
      )}
      {lb !== null && <Lightbox c={c} i={lb} setI={setLb} />}
    </section>
  );
}
