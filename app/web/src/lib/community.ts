// Types and helpers for the Community pages (/api/community…, docs/community.md): the club layer on top of Together.
import type { PersonInfo } from "./people";
import type { TGroup } from "./together";
import { addDays, isoLocal } from "./format";

type N = number | null;

export type CatId = "intervals" | "tempo" | "long" | "easy" | "race" | "other";
export interface Category { id: CatId; label: string; description?: string; n: number; km?: number; hours?: number }

export interface Collective {
  km: number; moving_s: number; hours: number; reps: number; climb_m: number; kcal: number; n_members: number;
}
export interface FormInfo { delta: N; text?: string; confidence?: "high" | "medium" | "low" | string | null; discipline?: string; score?: N; shape?: N; kind?: string }
export interface Photo { thumb: string; url: string; w?: number; h?: number }

export interface CGroup extends TGroup {
  category: CatId; category_auto: CatId; category_manual: boolean; title: string | null; display_title: string; note: string | null;
  n_timed: number; timed: string[]; n_members: number; guests: string[]; n_guests: number;
  photo: Photo | null; n_photos: number; kudos: string[]; n_kudos: number; kudoed: boolean | null; mine: boolean | null; user_hidden: boolean;
  collective: Collective; form: Record<string, N>; form_info?: Record<string, FormInfo | null>;
}
export interface CList {
  n: number; viewer: string | null; built: string; people: Record<string, PersonInfo>; categories: Category[];
  form_available: boolean; store_error: string | null; groups: CGroup[];
}

export interface Attendance {
  person: string; display: PersonInfo; sessions: number; share: number; first_seen: string | null; last_seen: string | null; last_gid: string | null;
  days_since: N; streak_weeks: number; best_streak_weeks: number; weeks_active: number; km: number; hours: number; reps: number;
  by_category: Partial<Record<CatId, number>>; favourite_place: string | null; avg_form_delta: N; n_form: number;
  company?: CompanyShort;
}
export interface ClubMonth { month: string; sessions: number; km: number; hours: number; reps: number; climb_m: number;
  members: string[]; n_members: number; by_category: Partial<Record<CatId, number>>; gids: string[] }
export interface Highlight { kind: string; gid: string; date: string; person: string | null; other?: string | null; value: number; unit: string; text: string }
export interface Club {
  from: string | null; to: string | null; today: string; viewer: string | null; people: Record<string, PersonInfo>; form_available: boolean;
  totals: { sessions: number; km: number; hours: number; reps: number; climb_m: number; kcal: number;
    members: number; guests: number; guest_visits: number; photos: number; kudos: number; places: number; first: string | null; last: string | null;
    this_year: { year: string; sessions: number; km: number; hours: number } };
  months: ClubMonth[]; attendance: Attendance[];
  categories: Category[]; highlights: Highlight[];
  places: { id: string; name: string; sessions: number; last: string }[]; guests: { name: string; visits: number }[];
  company: Record<string, CompanyShort>; goal: Goal; goals: Goal[];
  goal_metrics: Goals["metrics"];
}
export interface MemberSession {
  gid: string; date: string; start_local: string; place: string | null; workout: string; group_workout: string; title: string | null; category: CatId;
  with: string[]; role: string; form: (FormInfo & { score: number; shape: number; delta: number; date?: string }) | null;
  numbers: { km: N; moving_s: N; dur_s: N; climb_m: N; kcal: N; reps: N; avg_hr: N } | null;
  rank: N; of_people: N; reps_done: N; avg_s: N; pace_s_km: N; n_photos: number; kudos: number;
}
export interface Member {
  person: string; display: PersonInfo; viewer: string | null; form_available: boolean;
  totals: { sessions: number; km: number; hours: number; reps: number; climb_m: number;
    first: string | null; last: string | null; streak_weeks: number; best_streak_weeks: number; weeks_active: number; avg_form_delta: N };
  by_category: Partial<Record<CatId, number>>; sessions: MemberSession[];
  form_series: { date: string; gid: string; score: number; shape: number; delta: number; discipline: string; confidence?: string; text?: string; kind?: string }[];
  partners: { person: string; display: PersonInfo; sessions: number; last: string; gids: string[] }[];
  company: Company;
}

/** session types (the API calls them categories): fixed order + fallback labels */
export const CATS: { id: CatId; label: string }[] = [
  { id: "intervals", label: "Intervals" },
  { id: "tempo", label: "Tempo" },
  { id: "long", label: "Long run" },
  { id: "easy", label: "Easy" },
  { id: "race", label: "Race" },
  { id: "other", label: "Other" },
];
export const catInfo = (id: string) => CATS.find((c) => c.id === id) ?? CATS[CATS.length - 1];

/** form delta "+2.4" / "−1.6" / "±0.0" */
export function delta(v: N | undefined, d = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  const r = Number(v.toFixed(d));
  return (r > 0 ? "+" : r < 0 ? "−" : "±") + Math.abs(r).toFixed(d);
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-10" -> "October 2026" (short: "Oct 26") */
export const monthLabel = (m: string, short = false) =>
  short ? `${MON[Number(m.slice(5, 7)) - 1]} ${m.slice(2, 4)}` : `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;

/** rows grouped by month (input newest first, order kept) */
export function byMonth<T extends { date: string }>(rows: T[]): { month: string; rows: T[] }[] {
  const out: { month: string; rows: T[] }[] = [];
  for (const r of rows) {
    const m = r.date.slice(0, 7);
    if (out.length && out[out.length - 1].month === m) out[out.length - 1].rows.push(r);
    else out.push({ month: m, rows: [r] });
  }
  return out;
}

/** "12 days ago" / "today" / "yesterday" */
export function ago(days: N | undefined): string {
  if (days === null || days === undefined) return "—";
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 70) return `${Math.round(days / 7)} weeks ago`;
  if (days < 700) return `${Math.round(days / 30.4)} months ago`;
  return `${(days / 365).toFixed(1)} years ago`;
}
/** hours with one decimal under 100 ("45.7 h"), whole above */
export const hrs = (h: N | undefined) => (h === null || h === undefined ? "—" : `${h >= 100 ? Math.round(h) : h.toFixed(1)} h`);
/** km with one decimal under 100 */
export const kms = (k: N | undefined) => (k === null || k === undefined ? "—" : `${k >= 100 ? Math.round(k).toLocaleString("en-GB") : k.toFixed(1)} km`);
export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/** periods for the filters: last 90 days, last 12 months, each year (newest first) */
export function periods(years: string[]) {
  const today = isoLocal();
  return [
    { id: "90d", label: "Last 90 days", from: addDays(today, -89), to: "" },
    { id: "12m", label: "Last 12 months", from: addDays(today, -364), to: "" },
    ...years.map((y) => ({ id: y, label: y, from: `${y}-01-01`, to: `${y}-12-31` })),
  ];
}
export const HIGHLIGHT: Record<string, string> = {
  closest_finish: "Closest finish", form_jump: "Biggest form jump", longest_session: "Longest session", most_km: "Most km in one go",
  most_reps: "Most reps", most_climb: "Most climbing", biggest_turnout: "Biggest turnout",
};
/** a period id -> {from, to, label}: "90d", "12m", "2025", or a month "2026-10" (the club chart links there) */
export function resolvePeriod(id: string): { from: string; to: string; label: string } | null {
  if (!id) return null;
  const m = /^(\d{4})-(\d{2})$/.exec(id);
  if (m) {
    const last = new Date(Date.UTC(Number(m[1]), Number(m[2]), 0)).getUTCDate();
    return { from: `${id}-01`, to: `${id}-${String(last).padStart(2, "0")}`, label: monthLabel(id) };
  }
  if (/^\d{4}$/.test(id)) return { from: `${id}-01-01`, to: `${id}-12-31`, label: id };
  const p = periods([]).find((x) => x.id === id);
  return p ? { from: p.from, to: p.to, label: p.label } : null;
}

// ---- motivation: company effect, goals (docs/community.md "Motivation")
export interface CompanyPace { family: "short" | "medium" | "long"; label: string; diff_s_km: number; text: string; n_joint: number; n_solo: number; joint_s_km: number; solo_s_km: number;
  joint?: { gid: string; date: string; pace_s_km: number }[]; solo?: { activity_id: string; date: string; pace_s_km: number }[] }
export interface CompanyShort { diff: N; verdict: "company" | "alone" | "even" | null; text: string | null; n_joint: number; n_solo: number; n_compared: number; n_joint_cmp?: number; n_solo_cmp?: number;
  joint_avg: N; solo_avg: N; thin: boolean; pace: CompanyPace[] }
export interface Company extends CompanyShort {
  person: string; window_days: number;
  by_discipline: { discipline: string; n_joint: number; n_solo: number; joint_avg: N; solo_avg: N; diff: N }[];
  joint: { gid: string; date: string; delta: number; discipline: string; confidence?: string }[];
  solo: { activity_id: string; date: string; delta: number; discipline: string; confidence?: string; type?: string; name?: string }[];
}
export type GoalMetric = "sessions" | "km" | "hours" | "reps" | "climb_m";
export interface Goal { id: string; title: string; metric: GoalMetric; target: number; from: string; to: string; default?: boolean; unit: string; metric_label: string;
  value: number; pct: number; expected: number; expected_pct: number; remaining: number; days: number; days_left: number;
  status: "done" | "ahead" | "on_track" | "behind" | "ended" | "upcoming"; text: string; n_sessions: number; last_gid: string | null;
  by_member: { person: string; value: number }[] }
export interface Goals { goal: Goal; goals: Goal[]; metrics: { id: GoalMetric; unit: string; label: string }[] }
/** "3:19" per km from seconds per km */
export const paceKm = (s: N | undefined) => (s === null || s === undefined ? "—" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
