// One day: its activities first, then the calendar, where the user was (all of that day's tracks), daily summary,
// sleep and intraday HR. Missing daily data (summary / sleep) is one muted line, not empty blocks.
import { useEffect, useMemo, useState } from "preact/hooks";
import { useApi, type CalDay, type Day, type Meta, type Series } from "../lib/api";
import { navigate } from "../lib/router";
import { addDays, dur, hm, isoLocal, longDate, num } from "../lib/format";
import { YearCalendar } from "../components/Calendar";
import { TracksMap } from "../components/TracksMap";
import { Table } from "../components/Table";
import { activityCols, togetherWith } from "../components/ActivityBits";
import { PageHeader } from "../components/PageHeader";
import { SeriesCharts } from "../components/SeriesCharts";

const clock = (s: number) => `${String(Math.floor(s / 3600) % 24).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;

function DayHR({ day }: { day: Day }) {
  const series = useMemo<Series | null>(() => {
    if (!day.hr) return null;
    const mid = new Date(day.date + "T00:00:00").getTime();
    const ts = day.hr.values.map((v) => Math.round((v[0] - mid) / 1000)), hs = day.hr.values.map((v) => v[1] as number | null);
    // no line across missing data: a null sample in every gap longer than 2 × the usual step
    const steps = ts.slice(1).map((x, i) => x - ts[i]).sort((a, b) => a - b);
    const step = steps[steps.length >> 1] || 120;
    const t: number[] = [], hr: (number | null)[] = [];
    ts.forEach((x, i) => {
      if (i && x - ts[i - 1] > 2 * step) { t.push(ts[i - 1] + step); hr.push(null); }
      t.push(x); hr.push(hs[i]);
    });
    return { cols: ["t", "hr"], t, hr, start_utc: 0 };
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
  const cols = useMemo(() => activityCols(), []);

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
  useEffect(() => { document.title = date ? `Day ${longDate(date)}` : "Day"; }, [date]);

  if (!date) return <main><div class="empty">Loading…</div></main>;
  const year = Number(date.slice(0, 4));
  const go = (d: string | null) => d && navigate(`/day/${d}`);
  const sm = data?.summary, sl = data?.sleep;
  const joint = (data?.activities ?? []).filter((a) => a.together);
  const recent = date >= addDays(isoLocal(), -3);   // summaries / sleep of the last days are often not downloaded yet
  const daily = !!(sm || sl);
  // one muted line for what's missing (none while loading)
  const missing = !data ? "" : !sm && !sl ? (recent ? "Daily data not downloaded yet" : "No daily data downloaded for this day")
    : !sm ? (recent ? "Daily summary not downloaded yet" : "No daily summary downloaded")
    : !sl ? (recent ? "Sleep not downloaded yet" : "No sleep data downloaded") : "";
  const tracks = data?.tracks ?? [];
  // empty day: show where the last active day was (muted), not always Oslo
  const prev = useApi<Day>(data && !tracks.length && data.prev_active ? `/api/days/${data.prev_active}` : null).data;
  const shownTracks = useMemo(() => (tracks.length ? tracks : (prev?.tracks ?? []).map((t) => ({ ...t, muted: true }))), [tracks, prev]);
  const emptyNote = !data ? "Loading…" : tracks.length ? "" : `${data.activities.length ? "No GPS tracks this day" : "No activities this day"}${prev?.tracks.length ? ` · showing ${prev.date}` : ""}`;

  return (
    <main>
      <section class="first">
        <PageHeader title={longDate(date)} sub={data ? `${data.activities.length} ${data.activities.length === 1 ? "activity" : "activities"}` : ""}
          about="One day: where you went, what you did, how you slept and your heart rate through the day. The calendar picks the day."
          tips={["← / → keys step one day", "Hover an activity to find it on the map; click to open it", "Calendar colour = main sport, darker = longer"]}>
            <button onClick={() => go(addDays(date, -1))} title="Previous day (←)">← Day</button>
            <input type="date" value={date} onChange={(e) => go((e.target as HTMLInputElement).value)} />
            <button onClick={() => go(addDays(date, 1))} title="Next day (→)">Day →</button>
        </PageHeader>
        {joint.length > 0 && <p class="day-together">{joint.map((a) => (
          <a key={a.id} href={`/community/${a.together!.gid}`}>{togetherWith(a.together!)}{a.together!.workout ? ` · ${a.together!.workout}` : ""}</a>))}</p>}
        {error && <div class="err">{/400|YYYY/.test(error) ? `“${date}” is not a date (use YYYY-MM-DD).` : `Could not load this day: ${error}`}</div>}
        <h2 style={{ marginBottom: "8px" }}>Activities</h2>
        <Table cols={cols} rows={data?.activities ?? []} rowKey={(a) => a.id} highlight={hl} onHover={(a) => setHl(a?.id ?? null)}
          onRow={(a) => navigate(`/activities/${a.id}`)} empty={data ? "No activities this day." : "Loading…"} />
      </section>

      <section>
        <div class="head">
          <select value={String(year)} onChange={(e) => {
            const y = (e.target as HTMLSelectElement).value;
            const first = cal?.days.filter((d) => d.date.startsWith(y)).slice(-1)[0];
            go(first ? first.date : `${y}-01-01`);
          }}>
            {(meta?.years ?? [String(year)]).map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
        {cal && <YearCalendar year={year} days={calMap} selected={date} onPick={(d) => go(d)} />}
      </section>

      <section>
        <div class={daily ? "cols" : ""} style={daily ? { gridTemplateColumns: "minmax(0, 3fr) minmax(300px, 2fr)" } : undefined}>
          <TracksMap tracks={shownTracks} highlight={hl} onHover={setHl} onClick={(id) => navigate(`/activities/${id}`)} class="bordered" style={{ height: "460px" }}
            markers={!!tracks.length} empty="">
            {emptyNote && <div class="overlay bl">{emptyNote}</div>}
          </TracksMap>
          {daily && <div>
            {sm && <>
              <h2 style={{ marginBottom: "8px" }}>Day summary</h2>
              <dl class="kv two">
                <dt>Steps</dt><dd>{num(sm.steps)}</dd>
                <dt>Resting HR</dt><dd>{num(sm.rhr)} <span class="muted">min {num(sm.minhr)}, max {num(sm.maxhr)}</span></dd>
                <dt>Stress</dt><dd>{num(sm.stress)}</dd>
                <dt>Body battery</dt><dd>{sm.bbmax !== null ? `${num(sm.bbmin)} – ${num(sm.bbmax)}` : "—"}</dd>
                <dt>Intensity min</dt><dd>{num(sm.mod)} moderate, {num(sm.vig)} vigorous</dd>
                <dt>Calories</dt><dd>{num(sm.kcal)}</dd>
                <dt>Floors</dt><dd>{num(sm.floors)}</dd>
              </dl>
            </>}
            {sl && <>
              <h2 style={{ margin: sm ? "16px 0 8px" : "0 0 8px" }}>Sleep</h2>
              <dl class="kv two">
                <dt>Total</dt><dd>{hm(sl.total)} h <span class="muted">score {num(sl.score)}</span></dd>
                <dt>Deep / light / REM</dt><dd>{hm(sl.deep)} / {hm(sl.light)} / {hm(sl.rem)}</dd>
                <dt>Awake</dt><dd>{dur(sl.awake)}</dd>
                <dt>Avg HR</dt><dd>{num(sl.hr)}</dd>
              </dl>
            </>}
            {missing && <p class="muted">{missing}</p>}
          </div>}
        </div>
        {!daily && missing && <p class="muted">{missing}</p>}
      </section>

      {data?.hr && (
        <section>
          <div class="head"><h2 title="2-minute values">Heart rate through the day</h2><span class="sub">resting {num(data.hr.rhr)}</span></div>
          <DayHR day={data} />
        </section>
      )}
    </main>
  );
}
