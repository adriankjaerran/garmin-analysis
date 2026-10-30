// Dense sortable table (Fabstack console rules: sticky header, hairlines, tabular numbers).
import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

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
}

export function Table<T>({ cols, rows, rowKey, onRow, onHover, selected, highlight, defaultSort, maxHeight, empty, follow }: Props<T>) {
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

  if (!rows.length) return <div class="empty">{empty ?? "Nothing here."}</div>;
  return (
    <div class="tablewrap" ref={wrap} style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} class={(c.num ? "num " : "") + (sort?.[0] === c.key ? "sorted" : "")} title={c.title}
                style={c.width ? { width: c.width + "px" } : undefined}
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
                {cols.map((c) => (
                  <td key={c.key} class={c.num ? "num" : ""}>{c.render ? c.render(r, i) : fmtCell((r as any)[c.key])}</td>
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
