// Dense sortable table (Fabstack console rules: sticky header, hairlines, tabular numbers).
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
}

export function Table<T>({ cols, rows, rowKey, onRow, onHover, selected, highlight, defaultSort, maxHeight, empty, follow, stickyCols = 1 }: Props<T>) {
  const [sort, setSort] = useState<[string, 1 | -1] | null>(defaultSort ?? null);
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
              <th key={c.key} class={cellCls(ci, c.num) + (sort?.[0] === c.key ? " sorted" : "") + (c.cls ? " " + c.cls : "")} title={c.title}
                style={{ ...(c.width ? { width: c.width + "px" } : {}), ...cellStyle(ci) }}
                onClick={() => setSort(sort?.[0] === c.key ? [c.key, (sort[1] * -1) as 1 | -1] : [c.key, c.num ? -1 : 1])}>
                {c.label}{sort?.[0] === c.key ? (sort[1] === 1 ? " ▲" : " ▼") : ""}
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
                  <td key={c.key} class={cellCls(ci, c.num) + (c.cls ? " " + c.cls : "")} style={cellStyle(ci)}>{c.render ? c.render(r, i) : fmtCell((r as any)[c.key])}</td>
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
