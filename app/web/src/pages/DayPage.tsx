// Calendar + one day: where the user was (all of that day's tracks), daily summary, sleep, intraday HR.
import { useEffect, useMemo, useState } from "preact/hooks";
import { useApi, type CalDay, type Day, type Meta, type Series } from "../lib/api";
import { navigate } from "../lib/router";
import { addDays, dayLabel, dur, hm, num } from "../lib/format";
import { YearCalendar } from "../components/Calendar";
import { TracksMap } from "../components/TracksMap";
import { Table } from "../components/Table";
import { activityCols } from "../components/ActivityBits";
import { SeriesCharts } from "../components/SeriesCharts";

const clock = (s: number) => `${String(Math.floor(s / 3600) % 24).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;

function DayHR({ day }: { day: Day }) {
  const series = useMemo<Series | null>(() => {
    if (!day.hr) return null;
    const mid = new Date(day.date + "T00:00:00").getTime();
    return { cols: ["t", "hr"], t: day.hr.values.map((v) => Math.round((v[0] - mid) / 1000)), hr: day.hr.values.map((v) => v[1]), start_utc: 0 };
  }, [day]);
  if (!series) return null;
  return <SeriesCharts series={series} panels={["hr"]} xFmt={clock} />;
}

export function DayPage({ date }: { date: string | null }) {
  const meta = useApi<Meta>("/api/meta").data;
  useEffect(() => { if (!date && meta) navigate(`/day/${meta.last}`, true); }, [date, meta]);
  const cal = useApi<{ days: CalDay[] }>("/api/calendar").data;
  const calMap = useMemo(() => new Map((cal?.days ?? []).map((d) => [d.date, d])), [cal]);
  const { data, error } = useApi<Day>(date ? `/api/days/${date}` : null);
  const [hl, setHl] = useState<string | null>(null);
  const cols = useMemo(() => activityCols({ date: false }), []);

  useEffect(() => {
    if (!date) return;
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "ArrowLeft") navigate(`/day/${addDays(date, -1)}`);
      if (e.key === "ArrowRight") navigate(`/day/${addDays(date, 1)}`);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [date]);
  useEffect(() => { document.title = date ? `Day ${date}` : "Day"; }, [date]);

  if (!date) return <main><div class="empty">Loading…</div></main>;
  const year = Number(date.slice(0, 4));
  const go = (d: string | null) => d && navigate(`/day/${d}`);
  const sm = data?.summary, sl = data?.sleep;
  const tracks = data?.tracks ?? [];

  return (
    <main>
      <section class="first">
        <div class="head">
          <h1>{dayLabel(date)}</h1>
          <span class="sub">{data ? `${data.activities.length} ${data.activities.length === 1 ? "activity" : "activities"}` : ""}</span>
          <div class="tools">
            <button disabled={!data?.prev_active} onClick={() => go(data?.prev_active ?? null)} title="Previous day with an activity">⇤ Prev active</button>
            <button onClick={() => go(addDays(date, -1))} title="Previous day (←)">← Day</button>
            <input type="date" value={date} onChange={(e) => go((e.target as HTMLInputElement).value)} />
            <button onClick={() => go(addDays(date, 1))} title="Next day (→)">Day →</button>
            <button disabled={!data?.next_active} onClick={() => go(data?.next_active ?? null)} title="Next day with an activity">Next active ⇥</button>
          </div>
        </div>
        <div class="head">
          <select value={String(year)} onChange={(e) => {
            const y = (e.target as HTMLSelectElement).value;
            const first = cal?.days.filter((d) => d.date.startsWith(y)).slice(-1)[0];
            go(first ? first.date : `${y}-01-01`);
          }}>
            {(meta?.years ?? [String(year)]).map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <span class="sub">Click a day; colour = main sport, darker = longer</span>
        </div>
        {cal && <YearCalendar year={year} days={calMap} selected={date} onPick={(d) => go(d)} />}
      </section>

      <section>
        {error && <div class="err">{error}</div>}
        <div class="cols" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(300px, 2fr)" }}>
          <TracksMap tracks={tracks} highlight={hl} onHover={setHl} onClick={(id) => navigate(`/activities/${id}`)} class="bordered" style={{ height: "460px" }}
            empty={data ? (data.activities.length ? "No GPS tracks this day" : "No activities this day") : "Loading…"} />
          <div>
            <h2 style={{ marginBottom: "8px" }}>Day summary</h2>
            {sm ? (
              <dl class="kv two">
                <dt>Steps</dt><dd>{num(sm.steps)}</dd>
                <dt>Resting HR</dt><dd>{num(sm.rhr)} <span class="muted">min {num(sm.minhr)}, max {num(sm.maxhr)}</span></dd>
                <dt>Stress</dt><dd>{num(sm.stress)}</dd>
                <dt>Body battery</dt><dd>{sm.bbmax !== null ? `${num(sm.bbmin)} – ${num(sm.bbmax)}` : "—"}</dd>
                <dt>Intensity min</dt><dd>{num(sm.mod)} moderate, {num(sm.vig)} vigorous</dd>
                <dt>Calories</dt><dd>{num(sm.kcal)}</dd>
                <dt>Floors</dt><dd>{num(sm.floors)}</dd>
              </dl>
            ) : <div class="empty">No daily summary for this day.</div>}
            <h2 style={{ margin: "16px 0 8px" }}>Sleep</h2>
            {sl ? (
              <dl class="kv two">
                <dt>Total</dt><dd>{hm(sl.total)} h <span class="muted">score {num(sl.score)}</span></dd>
                <dt>Deep / light / REM</dt><dd>{hm(sl.deep)} / {hm(sl.light)} / {hm(sl.rem)}</dd>
                <dt>Awake</dt><dd>{dur(sl.awake)}</dd>
                <dt>Avg HR</dt><dd>{num(sl.hr)}</dd>
              </dl>
            ) : <div class="empty">No sleep data downloaded for this night.</div>}
          </div>
        </div>
      </section>

      <section>
        <div class="head"><h2>Activities</h2><span class="sub">Hover to highlight on the map; click to open</span></div>
        <Table cols={cols} rows={data?.activities ?? []} rowKey={(a) => a.id} highlight={hl} onHover={(a) => setHl(a?.id ?? null)}
          onRow={(a) => navigate(`/activities/${a.id}`)} empty={data ? "No activities this day." : "Loading…"} />
      </section>

      {data?.hr && (
        <section>
          <div class="head"><h2>Heart rate through the day</h2><span class="sub">2-minute values, resting {num(data.hr.rhr)}</span></div>
          <DayHR day={data} />
        </section>
      )}
    </main>
  );
}
