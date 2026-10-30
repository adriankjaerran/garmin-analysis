// Year heat calendar (weeks x weekdays, Monday first). Cell colour = dominant sport, opacity = time.
import { useMemo } from "preact/hooks";
import type { CalDay } from "../lib/api";
import { SPORT_LABEL, dur, sportColor } from "../lib/format";

const C = 12, G = 2, LEFT = 28, TOP = 16;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function YearCalendar({ year, days, selected, onPick }: { year: number; days: Map<string, CalDay>; selected: string | null; onPick: (d: string) => void }) {
  const cells = useMemo(() => {
    const out: { date: string; x: number; y: number; d?: CalDay }[] = [];
    const jan1 = new Date(Date.UTC(year, 0, 1));
    const off = (jan1.getUTCDay() + 6) % 7; // Monday = 0
    for (let t = jan1; t.getUTCFullYear() === year; t = new Date(t.getTime() + 864e5)) {
      const doy = Math.round((t.getTime() - jan1.getTime()) / 864e5);
      const date = t.toISOString().slice(0, 10);
      out.push({ date, x: Math.floor((doy + off) / 7), y: (t.getUTCDay() + 6) % 7, d: days.get(date) });
    }
    return out;
  }, [year, days]);
  const weeks = Math.max(...cells.map((c) => c.x)) + 1;
  const months = MONTHS.map((m, i) => ({ m, x: cells.find((c) => c.date === `${year}-${String(i + 1).padStart(2, "0")}-01`)!.x }));
  const W = LEFT + weeks * (C + G), H = TOP + 7 * (C + G);
  const total = cells.reduce((s, c) => s + (c.d?.s ?? 0), 0), n = cells.filter((c) => c.d).length;
  return (
    <div class="cal">
      <svg width={W} height={H} role="img" aria-label={`Activity calendar ${year}`}>
        {months.map((m) => <text key={m.m} x={LEFT + m.x * (C + G)} y={11}>{m.m}</text>)}
        {["Mon", "", "Wed", "", "Fri", "", "Sun"].map((l, i) => l && <text key={l} x={0} y={TOP + i * (C + G) + 10}>{l}</text>)}
        {cells.map((c) => {
          let fill = "var(--surface-tint)", op = 1;
          if (c.d) {
            const top = Object.entries(c.d.sports).sort((a, b) => b[1] - a[1])[0][0];
            fill = sportColor(top);
            op = Math.min(1, 0.3 + c.d.s / 7200);
          }
          return (
            <rect key={c.date} class={"d" + (c.date === selected ? " sel" : "")} x={LEFT + c.x * (C + G)} y={TOP + c.y * (C + G)} width={C} height={C} rx={2}
              fill={fill} fill-opacity={op} onClick={() => onPick(c.date)}>
              <title>{c.date}{c.d ? ` · ${c.d.n} ${c.d.n === 1 ? "activity" : "activities"}, ${c.d.km} km, ${dur(c.d.s)} · ${Object.keys(c.d.sports).map((s) => SPORT_LABEL[s]).join(", ")}` : ""}</title>
            </rect>
          );
        })}
      </svg>
      <div class="note">{n} active days, {Math.round(total / 3600)} h in {year}</div>
    </div>
  );
}
