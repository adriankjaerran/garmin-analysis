// Dense sortable table (Fabstack console rules: sticky header, hairlines, tabular numbers).
// Columns can be reordered (drag a header) and resized (drag a header's right edge; double-click the edge to reset);
// order and widths are remembered per table in localStorage (key `table.<storeKey>`, default = the column keys).
import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";
import "./table.css";

export interface Col<T> {
  key: string;
  label: string;
  num?: boolean;
  /** cell content; default String(row[key]) */
  render?: (r: T, i: number) => ComponentChildren;
  /** sort value; default row[key] */
  sort?: (r: T) => number | string | null | undefined;
  title?: string;
  width?: number;
  /** extra class on the column's header and cells (e.g. a highlighted column) */
  cls?: string;
}

interface Props<T> {
  cols: Col<T>[];
  rows: T[];
  rowKey: (r: T, i: number) => string;
  onRow?: (r: T) => void;
  onHover?: (r: T | null) => void;
  selected?: string | null;
  highlight?: string | null;
  defaultSort?: [string, 1 | -1];
  maxHeight?: string;
  empty?: string;
  /** scroll the selected row into view when it changes */
  follow?: boolean;
  /** leading columns that stay visible while the table scrolls sideways (default 1, 0 = none; only active
   *  when the table is wider than its box). E.g. 2 for [checkbox, date] */
  stickyCols?: number;
  /** localStorage key for the user's column order / widths (default: the column keys joined) */
  storeKey?: string;
}

interface Layout { order?: string[]; widths?: Record<string, number> }
const loadLayout = (k: string): Layout => { try { return JSON.parse(localStorage.getItem(k) ?? "{}") ?? {}; } catch { return {}; } };

export function Table<T>({ cols: colsIn, rows, rowKey, onRow, onHover, selected, highlight, defaultSort, maxHeight, empty, follow, stickyCols = 1, storeKey }: Props<T>) {
  const [sort, setSort] = useState<[string, 1 | -1] | null>(defaultSort ?? null);
  // user layout: column order + widths (persisted)
  const lkey = "table." + (storeKey ?? colsIn.map((c) => c.key).join(","));
  const [layout, setLayoutState] = useState<Layout>(() => loadLayout(lkey));
  useEffect(() => { setLayoutState(loadLayout(lkey)); }, [lkey]);
  const setLayout = (f: (l: Layout) => Layout) => setLayoutState((l) => {
    const n = f(l);
    try { localStorage.setItem(lkey, JSON.stringify(n)); } catch { /* full / private */ }
    return n;
  });
  const cols = useMemo(() => {
    const ord = layout.order;
    if (!ord?.length) return colsIn;
    const byKey = new Map(colsIn.map((c) => [c.key, c]));
    const out = ord.map((k) => byKey.get(k)).filter(Boolean) as Col<T>[];
    for (const c of colsIn) if (!ord.includes(c.key)) out.splice(colsIn.indexOf(c), 0, c);   // new columns at their default spot
    return out;
  }, [colsIn, layout.order]);
  const widthOf = (c: Col<T>) => layout.widths?.[c.key] ?? c.width;
  const [drag, setDrag] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const resizing = useRef(false);
  const moveCol = (from: string, to: string) => {
    if (from === to) return;
    const keys = cols.map((c) => c.key).filter((k) => k !== from);
    keys.splice(keys.indexOf(to) + (cols.findIndex((c) => c.key === from) < cols.findIndex((c) => c.key === to) ? 1 : 0), 0, from);
    setLayout((l) => ({ ...l, order: keys }));
  };
  const startResize = (e: PointerEvent, key: string) => {
    e.preventDefault(); e.stopPropagation();
    const th = (e.currentTarget as HTMLElement).parentElement as HTMLElement;
    const x0 = e.clientX, w0 = th.getBoundingClientRect().width;
    resizing.current = true;
    const mv = (ev: PointerEvent) => {
      const w = Math.max(36, Math.round(w0 + ev.clientX - x0));
      setLayoutState((l) => ({ ...l, widths: { ...l.widths, [key]: w } }));
    };
    const up = () => {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      setLayout((l) => l);   // persist
      setTimeout(() => { resizing.current = false; }, 0);   // swallow the click that ends the drag (no sort)
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  };
  const resetWidth = (key: string) => setLayout((l) => { const w = { ...l.widths }; delete w[key]; return { ...l, widths: w }; });
  const wrap = useRef<HTMLDivElement>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const c = cols.find((c) => c.key === sort[0]);
    if (!c) return rows;
    const val = c.sort ?? ((r: T) => (r as any)[c.key]);
    return [...rows].sort((a, b) => {
      const x = val(a), y = val(b);
      if (x === y) return 0;
      if (x === null || x === undefined || x === "") return 1;
      if (y === null || y === undefined || y === "") return -1;
      return (x < y ? -1 : 1) * sort[1];
    });
  }, [rows, sort, cols]);

  useEffect(() => {
    if (!follow || !selected || !wrap.current) return;
    const tr = wrap.current.querySelector(`tr[data-k="${CSS.escape(selected)}"]`) as HTMLElement | null;
    if (!tr) return;
    const w = wrap.current, top = tr.offsetTop - 32, bottom = tr.offsetTop + tr.offsetHeight;
    if (top < w.scrollTop || bottom > w.scrollTop + w.clientHeight) w.scrollTop = top - w.clientHeight / 3;
  }, [selected, follow]);

  // horizontal overflow: fade on the clipped side(s) + sticky first column, so wide tables read as scrollable
  const [hx, setHx] = useState({ l: false, r: false, over: false });
  const [lefts, setLefts] = useState<number[]>([]);
  useLayoutEffect(() => {
    const w = wrap.current;
    if (!w) return;
    const upd = () => {
      const over = w.scrollWidth > w.clientWidth + 1;
      const n = { over, l: over && w.scrollLeft > 1, r: over && w.scrollLeft + w.clientWidth < w.scrollWidth - 1 };
      setHx((o) => (o.l === n.l && o.r === n.r && o.over === n.over ? o : n));
      // x offsets of the sticky columns = summed widths of the header cells before them
      const ths = [...w.querySelectorAll("thead th")].slice(0, Math.max(0, stickyCols - 1)) as HTMLElement[];
      const L = [0];
      for (const th of ths) L.push(L[L.length - 1] + th.getBoundingClientRect().width);
      setLefts((o) => (o.length === L.length && o.every((v, i) => Math.abs(v - L[i]) < 0.5) ? o : L));
    };
    upd();
    w.addEventListener("scroll", upd, { passive: true });
    const ro = new ResizeObserver(upd);
    ro.observe(w);
    if (w.firstElementChild) ro.observe(w.firstElementChild);
    return () => { w.removeEventListener("scroll", upd); ro.disconnect(); };
  }, [rows.length > 0, stickyCols]);
  const stk = hx.over ? stickyCols : 0;
  const cellCls = (ci: number, num?: boolean) =>
    (num ? "num" : "") + (ci < stk ? " stk" + (ci === stk - 1 ? " stk-last" : "") : "");
  const cellStyle = (ci: number) => (ci < stk ? { left: (lefts[ci] ?? 0) + "px" } : undefined);

  if (!rows.length) return <div class="empty">{empty ?? "Nothing here."}</div>;
  return (
    <div class={"tablewrap" + (hx.over ? " hx" : "") + (hx.l ? " fade-l" : "") + (hx.r ? " fade-r" : "")}
      ref={wrap} style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <thead>
          <tr>
            {cols.map((c, ci) => (
              <th key={c.key} class={cellCls(ci, c.num) + (sort?.[0] === c.key ? " sorted" : "") + (c.cls ? " " + c.cls : "")
                  + (drag === c.key ? " col-drag" : "") + (over === c.key && drag && drag !== c.key ? " col-over" : "")}
                title={c.title ? c.title + " · drag to move, drag the edge to resize" : "Drag to move, drag the edge to resize"}
                style={{ ...(widthOf(c) ? { width: widthOf(c) + "px", minWidth: widthOf(c) + "px", maxWidth: widthOf(c) + "px" } : {}), ...cellStyle(ci) }}
                draggable
                onDragStart={(e) => { if (resizing.current) { e.preventDefault(); return; } setDrag(c.key); e.dataTransfer?.setData("text/plain", c.key); if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"; }}
                onDragOver={(e) => { if (!drag) return; e.preventDefault(); if (over !== c.key) setOver(c.key); }}
                onDrop={(e) => { e.preventDefault(); if (drag) moveCol(drag, c.key); setDrag(null); setOver(null); }}
                onDragEnd={() => { setDrag(null); setOver(null); }}
                onClick={() => { if (resizing.current) return; setSort(sort?.[0] === c.key ? [c.key, (sort[1] * -1) as 1 | -1] : [c.key, c.num ? -1 : 1]); }}>
                {c.label}{sort?.[0] === c.key ? (sort[1] === 1 ? " ▲" : " ▼") : ""}
                <span class="col-rs" onPointerDown={(e) => startResize(e as unknown as PointerEvent, c.key)}
                  onClick={(e) => e.stopPropagation()} onDblClick={(e) => { e.stopPropagation(); resetWidth(c.key); }} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody onMouseLeave={onHover ? () => onHover(null) : undefined}>
          {sorted.map((r, i) => {
            const k = rowKey(r, i);
            return (
              <tr key={k} data-k={k} class={(onRow ? "clickable " : "") + (k === selected ? "selected " : "") + (k === highlight ? "hl" : "")}
                onClick={onRow ? () => onRow(r) : undefined} onMouseEnter={onHover ? () => onHover(r) : undefined}>
                {cols.map((c, ci) => (
                  <td key={c.key} class={cellCls(ci, c.num) + (c.cls ? " " + c.cls : "")}
                    style={{ ...(widthOf(c) && layout.widths?.[c.key] ? { maxWidth: widthOf(c) + "px", overflow: "hidden", textOverflow: "ellipsis" } : {}), ...cellStyle(ci) }}>{c.render ? c.render(r, i) : fmtCell((r as any)[c.key])}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const fmtCell = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
