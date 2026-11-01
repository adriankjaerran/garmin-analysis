// "Share" on a club session: a 1080 × 1350 PNG (4:5, what chat apps and Instagram show uncropped) drawn on a canvas
// in the browser — no libraries, no network beyond the page's own cover photo. Cover photo (else the category
// gradient with everyone's track sketched in white), category, title, date · time · place, the headline, a row per
// member (rank, colour avatar, workout, time, form delta), the collective numbers and the rep of the day.
// A preview dialog offers Download, Copy (clipboard image) and the system share sheet where there is one.
import { useEffect, useState } from "preact/hooks";
import { createPortal } from "preact/compat";
import { dur, longDate, speedText } from "../lib/format";
import { personInfo, personName } from "../lib/people";
import { MEDAL, repTime, type Replay, type TSession } from "../lib/together";
import { hms, hoursMin, type CsSession, type Facts } from "../lib/communitySession";

const W = 1080, H = 1350;
const FONT = `-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif`;
const GRAD: Record<string, [string, string, string]> = {
  intervals: ["#1d3c8a", "#2f5fd0", "#6a3fc2"], tempo: ["#7d2810", "#c2560c", "#b8323a"], long: ["#11505f", "#1f8fa6", "#2f5fd0"],
  easy: ["#185a30", "#2f8a4c", "#1f8fa6"], race: ["#7a4f00", "#b88a00", "#c2560c"], other: ["#33502b", "#5b7d3a", "#55667a"],
};
const f = (w: number, px: number) => `${w} ${px}px ${FONT}`;

function rr(x: CanvasRenderingContext2D, a: number, b: number, w: number, h: number, r: number) {
  x.beginPath(); x.moveTo(a + r, b); x.arcTo(a + w, b, a + w, b + h, r); x.arcTo(a + w, b + h, a, b + h, r); x.arcTo(a, b + h, a, b, r); x.arcTo(a, b, a + w, b, r); x.closePath();
}
/** text cut with "…" to fit maxW */
function fit(x: CanvasRenderingContext2D, t: string, maxW: number) {
  if (x.measureText(t).width <= maxW) return t;
  while (t.length > 1 && x.measureText(t + "…").width > maxW) t = t.slice(0, -1);
  return t + "…";
}
/** up to `lines` lines of word-wrapped text */
function wrap(x: CanvasRenderingContext2D, t: string, maxW: number, lines: number): string[] {
  const out: string[] = []; let cur = "";
  for (const w of t.split(/\s+/)) {
    const nx = cur ? cur + " " + w : w;
    if (x.measureText(nx).width <= maxW || !cur) cur = nx;
    else { out.push(cur); cur = w; if (out.length === lines - 1) break; }
  }
  const rest = t.slice(out.join(" ").length).trim();
  if (out.length < lines && rest) out.push(fit(x, rest, maxW));
  return out;
}
const loadImg = (src: string) => new Promise<HTMLImageElement | null>((res) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = src; });

interface Row { key: string; rank: number | null; workout: string; main: string; sub: string; delta: number | null; low: boolean }

function rows(s: TSession, c: CsSession): Row[] {
  const lbOf = (k: string) => s.intervals?.leaderboard.find((l) => l.person === k);
  const raceOf = (k: string) => s.continuous?.race?.find((r) => r.person === k);
  const sport = s.group.sport;
  const out = c.members.filter((m) => m.role !== "other").map((m): Row => {
    const lb = lbOf(m.key), race = raceOf(m.key), n = m.numbers;
    let main = "", sub = "";
    if (lb && lb.total_s !== null) {
      main = repTime(lb.total_s);
      sub = [`${lb.reps_done}/${lb.of} reps`, lb.avg_s ? `avg ${repTime(lb.avg_s)}` : null, lb.pace_s_km ? speedText(sport, 1000 / lb.pace_s_km) : null].filter(Boolean).join(" · ");
    } else if (race) {
      main = hms(race.time_s);
      sub = [race.dist_km ? `${race.dist_km.toFixed(2)} km` : null, race.dist_km ? speedText(sport, (race.dist_km * 1000) / race.time_s) : null].filter(Boolean).join(" · ");
    } else {
      main = n?.moving_s ? dur(n.moving_s) : n?.km ? `${n.km.toFixed(1)} km` : "—";
      sub = [n?.km ? `${n.km.toFixed(1)} km` : null, n?.km && n?.moving_s ? speedText(sport, (n.km * 1000) / n.moving_s) : null].filter(Boolean).join(" · ");
    }
    const d = m.form && m.form.confidence !== "none" ? m.form.delta : null;
    const ranked = s.intervals ? s.intervals.leaderboard.filter((l) => l.total_s !== null).length > 1 : (s.continuous?.race?.length ?? 0) > 1;
    return { key: m.key, rank: ranked ? m.rank ?? race?.rank ?? null : null, workout: m.workout ?? "", main, sub, delta: d ?? null, low: m.form?.confidence === "low" };
  });
  return out.sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
}

export async function drawShareCard(s: TSession, c: CsSession, facts: Facts, replay: Replay | null): Promise<HTMLCanvasElement> {
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const x = cv.getContext("2d")!;
  const cat = c.category_info.id;
  const [g0, g1, g2] = GRAD[cat] ?? GRAD.intervals;
  const cover = c.meta.photos.find((p) => p.id === c.meta.cover) ?? null;
  const img = cover ? await loadImg(cover.url) : null;
  // the members panel and the numbers below it take what they need; the photo / sketch gets the rest (≥ 560)
  const R = rows(s, c);
  const show = R.slice(0, 6);
  const rowH = show.length <= 3 ? 112 : show.length <= 4 ? 100 : 84;
  const ph = 40 + show.length * rowH + (R.length > show.length ? 40 : 0);
  const TOP = Math.max(560, H - (30 + ph + 70 + 54 + 120));

  // background: the category gradient everywhere, the photo (if any) over the top part
  const bg = x.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, g0); bg.addColorStop(0.5, g1); bg.addColorStop(1, g2);
  x.fillStyle = bg; x.fillRect(0, 0, W, H);
  if (img) {
    const k = Math.max(W / img.width, TOP / img.height);
    const iw = img.width * k, ih = img.height * k;
    x.save(); x.beginPath(); x.rect(0, 0, W, TOP); x.clip();
    x.drawImage(img, (W - iw) / 2, (TOP - ih) / 2, iw, ih);
    x.restore();
    const sh = x.createLinearGradient(0, 0, 0, TOP);
    sh.addColorStop(0, "rgba(0,0,0,0.45)"); sh.addColorStop(0.35, "rgba(0,0,0,0.05)"); sh.addColorStop(0.7, "rgba(0,0,0,0.35)"); sh.addColorStop(1, g0);
    x.fillStyle = sh; x.fillRect(0, 0, W, TOP);
  } else if (replay) {
    // everyone's track, white, in the top right
    const pts = Object.values(replay.people).map((p) => {
      const o: [number, number][] = [];
      const st = Math.max(1, Math.floor(p.t.length / 800));
      for (let i = 0; i < p.t.length; i += st) if (p.lat[i] !== null && p.lon[i] !== null) o.push([p.lon[i]!, p.lat[i]!]);
      return o;
    }).filter((v) => v.length > 1);
    if (pts.length) {
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const v of pts) for (const [a, b] of v) { x0 = Math.min(x0, a); x1 = Math.max(x1, a); y0 = Math.min(y0, b); y1 = Math.max(y1, b); }
      const kx = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
      const bw = 560, bh = TOP - 330, sc = Math.min(bw / ((x1 - x0) * kx || 1e-6), bh / ((y1 - y0) || 1e-6));
      const ox = W - 60 - (bw + (x1 - x0) * kx * sc) / 2, oy = 120 + (bh - (y1 - y0) * sc) / 2;
      x.lineJoin = "round"; x.lineCap = "round";
      pts.forEach((v, j) => {
        x.beginPath();
        v.forEach(([a, b], i) => { const px = ox + (a - x0) * kx * sc, py = oy + (y1 - b) * sc; if (i) x.lineTo(px, py); else x.moveTo(px, py); });
        x.strokeStyle = j ? "rgba(255,255,255,0.28)" : "rgba(255,255,255,0.6)"; x.lineWidth = j ? 12 : 5; x.stroke();
      });
    }
  } else {
    x.globalAlpha = 0.14; x.font = f(400, 380); x.textAlign = "right"; x.textBaseline = "alphabetic";
    x.fillText(c.category_info.emoji, W + 30, 470); x.globalAlpha = 1;
  }

  const L = 64;
  x.textAlign = "left"; x.textBaseline = "alphabetic";
  // category chip + date
  x.font = f(600, 30);
  const chip = `${c.category_info.emoji}  ${c.category_info.label}`;
  const cw = x.measureText(chip).width + 44;
  x.fillStyle = "rgba(255,255,255,0.2)"; rr(x, L, 56, cw, 56, 28); x.fill();
  x.fillStyle = "#fff"; x.fillText(chip, L + 22, 95);
  x.textAlign = "right"; x.font = f(500, 30); x.fillStyle = "rgba(255,255,255,0.9)";
  x.fillText(`${s.group.start_local}`, W - L, 95); x.textAlign = "left";

  // title + when / where + headline (bottom of the top area)
  x.shadowColor = "rgba(0,0,0,0.35)"; x.shadowBlur = 12;
  x.font = f(700, 84);
  const title = c.meta.title || c.display_title || s.group.workout;
  const tl = wrap(x, title, W - 2 * L, 2);
  const place = c.place?.name ?? s.group.place?.name;
  const hl = s.headline?.text && s.headline.kind !== "unverified" ? s.headline.text.split(" · ").slice(0, 2).join(" · ") : "";
  let y = TOP - 40 - (hl ? 56 : 0) - 50 - (tl.length - 1) * 92;
  x.fillStyle = "#fff";
  for (const t of tl) { x.fillText(t, L, y); y += 92; }
  y -= 92 - 58;
  x.font = f(500, 34); x.fillStyle = "rgba(255,255,255,0.92)";
  x.fillText(fit(x, [longDate(c.date), place ? `📍 ${place}` : null].filter(Boolean).join("  ·  "), W - 2 * L), L, y);
  if (hl) {
    y += 56; x.font = f(700, 38); x.fillStyle = "#fff";
    x.fillText(fit(x, `${s.headline.kind === "race" ? "🏁" : s.headline.kind === "intervals" ? "🏆" : "👥"} ${hl}`, W - 2 * L), L, y);
  }
  x.shadowBlur = 0; x.shadowColor = "transparent";

  // members panel
  const py = TOP + 30;
  x.fillStyle = "rgba(255,255,255,0.97)"; rr(x, 40, py, W - 80, ph, 28); x.fill();
  let ry = py + 20;
  for (const r of show) {
    const cy = ry + rowH / 2;
    const info = personInfo(r.key);
    x.textBaseline = "middle";
    // rank
    x.font = f(600, 40); x.textAlign = "center"; x.fillStyle = "#5f6b7a";
    x.fillText(r.rank !== null ? MEDAL[r.rank - 1] ?? `${r.rank}` : "", 104, cy + 2);
    // avatar
    x.beginPath(); x.arc(176, cy, 34, 0, Math.PI * 2); x.fillStyle = info.colour; x.fill();
    x.fillStyle = "#fff"; x.font = f(700, 28); x.fillText(info.initials, 176, cy + 1);
    // name + sub
    x.textAlign = "left"; x.fillStyle = "#1d2125"; x.font = f(700, 38);
    const right = r.delta !== null ? 700 : 820;
    x.fillText(fit(x, personName(r.key), right - 240 - 190), 228, cy - 18);
    x.font = f(400, 26); x.fillStyle = "#5f6b7a";
    x.fillText(fit(x, r.sub || r.workout, right - 240), 228, cy + 22);
    // main value
    x.textAlign = "right"; x.font = f(700, 40); x.fillStyle = "#1d2125";
    x.fillText(r.main, right + (r.delta !== null ? 0 : 160), cy - 14);
    // form pill
    if (r.delta !== null) {
      const d = r.delta, t = `${d > 0 ? "+" : d < 0 ? "−" : "±"}${Math.abs(d).toFixed(1)}`;
      const col = r.low ? "#6b7480" : d >= 0.5 ? "#1f8a4c" : d <= -0.5 ? "#c43c3c" : "#2f5fd0";
      x.font = f(700, 32);
      const pw = Math.max(120, x.measureText(t).width + 40);
      x.fillStyle = col + "22"; rr(x, W - 64 - pw, cy - 44, pw, 54, 27); x.fill();
      x.fillStyle = col; x.textAlign = "center"; x.fillText(t, W - 64 - pw / 2, cy - 16);
      x.font = f(500, 20); x.fillStyle = "#6b7480"; x.fillText("vs form", W - 64 - pw / 2, cy + 28);
    }
    ry += rowH;
    if (r !== show[show.length - 1]) { x.fillStyle = "#e6e9ee"; x.fillRect(80, ry, W - 160, 2); }
  }
  if (R.length > show.length) { x.textAlign = "left"; x.font = f(500, 26); x.fillStyle = "#5f6b7a"; x.fillText(`+ ${R.length - show.length} more`, 228, ry + 20); }
  x.textBaseline = "alphabetic";

  // collective + rep of the day
  const k = c.collective;
  const verb = s.group.sport === "run" ? "running" : "moving";
  const bits = [k.km ? `${k.km.toFixed(1)} km together` : null, k.moving_s ? `${hoursMin(k.moving_s)} ${verb}` : null, k.reps ? `${k.reps} reps` : null,
    c.n_guests ? `+${c.n_guests} guest${c.n_guests === 1 ? "" : "s"}` : null].filter(Boolean);
  let by = py + ph + 70;
  x.textAlign = "left"; x.fillStyle = "#fff"; x.font = f(700, 40);
  x.fillText(fit(x, bits.join("  ·  "), W - 2 * L), L, by);
  const roday = facts.highlights.find((h) => h.id === "closest") ?? facts.highlights[0];
  if (roday && by + 60 < H - 60) {
    by += 54; x.font = f(500, 30); x.fillStyle = "rgba(255,255,255,0.88)";
    x.fillText(fit(x, `${roday.emoji} ${roday.title}: ${roday.value}${roday.sub ? ` — ${roday.sub}` : ""}`, W - 2 * L), L, by);
  }
  // footer
  x.font = f(500, 24); x.fillStyle = "rgba(255,255,255,0.7)"; x.textAlign = "right";
  x.fillText(`${c.n_timed} member${c.n_timed === 1 ? "" : "s"}${c.meta.kudos.length ? `  ·  👏 ${c.meta.kudos.length}` : ""}  ·  club session`, W - L, H - 44);
  return cv;
}

export function ShareButton({ s, c, facts, replay }: { s: TSession; c: CsSession; facts: Facts; replay: Replay | null }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button class="cs-share-btn" onClick={() => setOpen(true)} title="A picture of this session for the club's chat (PNG, 1080 × 1350)"><span aria-hidden="true">↗</span>Share</button>
      {open && createPortal(<ShareDialog s={s} c={c} facts={facts} replay={replay} onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

function ShareDialog({ s, c, facts, replay, onClose }: { s: TSession; c: CsSession; facts: Facts; replay: Replay | null; onClose: () => void }) {
  const [blob, setBlob] = useState<Blob | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    let live = true, u: string | null = null;
    drawShareCard(s, c, facts, replay).then((cv) => cv.toBlob((b) => {
      if (!live || !b) return;
      u = URL.createObjectURL(b); setBlob(b); setUrl(u);
    }, "image/png")).catch((e) => live && setMsg(`Could not draw the card: ${e}`));
    return () => { live = false; if (u) URL.revokeObjectURL(u); };
  }, []);
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);
  const name = `${c.date}-${(c.meta.title || c.display_title || "session").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").toLowerCase()}.png`;
  const copy = async () => {
    if (!blob) return;
    try { await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]); setMsg("Copied — paste it into the chat"); }
    catch (e) { setMsg(`Copy didn't work here (${(e as Error).message || e}); use Download`); }
  };
  const file = blob ? new File([blob], name, { type: "image/png" }) : null;
  const canShare = !!file && typeof navigator.canShare === "function" && navigator.canShare({ files: [file] });
  return (
    <div class="cs-share" role="dialog" aria-label="Share this session" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div class="cs-share-box">
        <div class="cs-share-head"><b>Share this session</b><span class="muted small">1080 × 1350 PNG · made on this computer</span>
          <button class="cs-x" onClick={onClose} aria-label="Close" title="Close (Esc)">×</button></div>
        <div class="cs-share-prev">{url ? <img src={url} alt="Share card preview" /> : <div class="empty">{msg ?? "Drawing…"}</div>}</div>
        <div class="cs-share-tools">
          <a class={"btn primary" + (url ? "" : " off")} href={url ?? undefined} download={name}>Download PNG</a>
          {"ClipboardItem" in window && <button onClick={copy} disabled={!blob}>Copy image</button>}
          {canShare && <button onClick={() => navigator.share({ files: [file!], title: c.meta.title || c.display_title }).catch(() => {})}>Share…</button>}
          {msg && url && <span class="small muted" role="status">{msg}</span>}
        </div>
      </div>
    </div>
  );
}
