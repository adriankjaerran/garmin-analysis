// Types and helpers for the Community pages (/api/community…, docs/community.md): the club layer on top of Together.
import type { PersonInfo } from "./people";
import type { H2H, TGroup } from "./together";
import { addDays, isoLocal } from "./format";

type N = number | null;

export type CatId = "intervals" | "tempo" | "long" | "easy" | "race" | "other";
export interface Category { id: CatId; label: string; emoji: string; description?: string; n: number; km?: number; hours?: number }

export interface Collective {
  km: number; moving_s: number; hours: number; reps: number; reps_shared: number; climb_m: number; kcal: number;
  person_min_together: number; person_min_shared: number; together_estimated: boolean; n_members: number;
}
export interface FormInfo { delta: N; text?: string; confidence?: "high" | "medium" | "low" | string | null; discipline?: string; score?: N; shape?: N; kind?: string }
export interface Photo { thumb: string; url: string; w?: number; h?: number }

export interface CGroup extends TGroup {
  category: CatId; category_auto: CatId; category_manual: boolean; title: string | null; display_title: string; note: string | null;
  n_timed: number; timed: string[]; n_members: number; guests: string[]; n_guests: number;
  photo: Photo | null; n_photos: number; kudos: string[]; n_kudos: number; kudoed: boolean | null; mine: boolean | null; user_hidden: boolean;
  collective: Collective; form: Record<string, N>; form_info?: Record<string, FormInfo | null>;
  n_records: number; records_short: string[];
}
export interface CTotals { sessions: number; km: number; hours: number; person_hours_together: number; reps: number; photos: number; guests: number }
export interface CList {
  n: number; viewer: string | null; built: string; people: Record<string, PersonInfo>; categories: Category[]; totals: CTotals;
  h2h: H2H[]; form_available: boolean; store_error: string | null; groups: CGroup[]; goal?: Goal;
}

export interface Attendance {
  person: string; display: PersonInfo; sessions: number; share: number; first_seen: string | null; last_seen: string | null; last_gid: string | null;
  days_since: N; streak_weeks: number; best_streak_weeks: number; weeks_active: number; km: number; hours: number; reps: number;
  by_category: Partial<Record<CatId, number>>; favourite_place: string | null; avg_form_delta: N; n_form: number;
  company?: CompanyShort; badges?: BadgesShort;
}
export interface ClubMonth { month: string; sessions: number; km: number; hours: number; person_hours_together: number; reps: number; climb_m: number;
  members: string[]; n_members: number; by_category: Partial<Record<CatId, number>>; gids: string[] }
export interface ClubRecord { measure: "race" | "avg" | "rep"; dist_m: number; label: string; value_s: number; text: string; person: string; gid: string; date: string;
  n_efforts: number; by_person: Record<string, { value_s: number; gid: string; date: string }> }
export interface Highlight { kind: string; gid: string; date: string; person: string | null; other?: string | null; value: number; unit: string; text: string }
export interface Club {
  from: string | null; to: string | null; today: string; viewer: string | null; people: Record<string, PersonInfo>; form_available: boolean;
  totals: { sessions: number; km: number; hours: number; person_hours_together: number; person_hours_shared: number; reps: number; climb_m: number; kcal: number;
    members: number; guests: number; guest_visits: number; photos: number; kudos: number; places: number; first: string | null; last: string | null;
    this_year: { year: string; sessions: number; km: number; hours: number } };
  months: ClubMonth[]; attendance: Attendance[]; most_loyal: { person: string; sessions: number } | null;
  pairs: { a: string; b: string; sessions: number; last: string; h2h: H2H }[];
  pair_matrix: { people: string[]; n: number[][] };
  categories: Category[]; records: ClubRecord[]; highlights: Highlight[];
  heatmap: { weekdays: string[]; hours: number[]; n: number[][] };
  places: { id: string; name: string; sessions: number; last: string }[]; guests: { name: string; visits: number }[];
  company: Record<string, CompanyShort>; rivalry: Rivalry[]; latest_badges: Badge[]; goal: Goal; goals: Goal[];
  goal_metrics: Goals["metrics"];
  recent: { gid: string; date: string; title: string; category: CatId; people: string[]; km: number; records: number }[];
}
export interface MemberSession {
  gid: string; date: string; start_local: string; place: string | null; workout: string; group_workout: string; title: string | null; category: CatId;
  with: string[]; role: string; form: (FormInfo & { score: number; shape: number; delta: number; date?: string }) | null;
  numbers: { km: N; moving_s: N; dur_s: N; climb_m: N; kcal: N; reps: N; avg_hr: N; with_min: N; shared_min: N } | null;
  rank: N; of_people: N; rep_wins: N; avg_s: N; pace_s_km: N; records: { text?: string; kind?: string }[]; n_photos: number; kudos: number;
}
export interface Effort { gid: string; date: string; measure: "race" | "avg" | "rep"; dist_m: number; label: string; value_s: number; n: number; rep?: string; dist_km?: number }
export interface ShapeDisc { label: string; unit: string; personal_best: N; current: { date: string; mean: number; sd: number; confidence?: string; pct_of_best?: number } | null;
  weekly: { date: string; mean: number; sd: number }[] }
export interface Member {
  person: string; display: PersonInfo; viewer: string | null; form_available: boolean;
  totals: { sessions: number; km: number; hours: number; reps: number; climb_m: number; min_together: number; records: number; club_records_held: number;
    first: string | null; last: string | null; streak_weeks: number; best_streak_weeks: number; weeks_active: number; avg_form_delta: N };
  by_category: Partial<Record<CatId, number>>; sessions: MemberSession[];
  form_series: { date: string; gid: string; score: number; shape: number; delta: number; discipline: string; confidence?: string; text?: string; kind?: string }[];
  partners: { person: string; display: PersonInfo; sessions: number; last: string; gids: string[]; rep_wins: number; rep_losses: number; rep_ties: number; h2h: H2H }[];
  best_sessions: MemberSession[]; personal_bests: Effort[];
  shape: { built: string; disciplines: Record<string, ShapeDisc> } | null;
  badges: Badges; company: Company; rivalry: Rivalry[];
}

/** fixed order + fallback labels (the API sends labels / emoji too) */
export const CATS: { id: CatId; label: string; emoji: string }[] = [
  { id: "intervals", label: "Intervals", emoji: "⏱️" },
  { id: "tempo", label: "Tempo", emoji: "🔥" },
  { id: "long", label: "Long run", emoji: "🛣️" },
  { id: "easy", label: "Easy", emoji: "🌿" },
  { id: "race", label: "Race", emoji: "🏁" },
  { id: "other", label: "Other", emoji: "🥾" },
];
export const catInfo = (id: string) => CATS.find((c) => c.id === id) ?? CATS[CATS.length - 1];
/** CSS colour of a category (community.css defines --cat-<id>, themed) */
export const catVar = (id: string) => `var(--cat-${CATS.some((c) => c.id === id) ? id : "other"})`;

/** form delta "+2.4" / "−1.6" / "±0.0" */
export function delta(v: N | undefined, d = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  const r = Number(v.toFixed(d));
  return (r > 0 ? "+" : r < 0 ? "−" : "±") + Math.abs(r).toFixed(d);
}
/** good / bad / flat class for a delta (|d| < 0.3 = flat) */
export const deltaCls = (v: N | undefined) => (v === null || v === undefined ? "none" : v >= 0.3 ? "up" : v <= -0.3 ? "down" : "flat");

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
/** "2026-10-20" -> unix seconds (noon UTC; chart x) */
export const dayTs = (d: string) => Date.parse(d + "T12:00:00Z") / 1000;

/** periods for the filters: last 90 days, last 12 months, each year (newest first) */
export function periods(years: string[]) {
  const today = isoLocal();
  return [
    { id: "90d", label: "Last 90 days", from: addDays(today, -89), to: "" },
    { id: "12m", label: "Last 12 months", from: addDays(today, -364), to: "" },
    ...years.map((y) => ({ id: y, label: y, from: `${y}-01-01`, to: `${y}-12-31` })),
  ];
}
export const MEASURE_LABEL: Record<string, string> = { race: "Race", avg: "Avg rep", rep: "Best rep" };
export const HIGHLIGHT: Record<string, { emoji: string; label: string }> = {
  closest_finish: { emoji: "🤏", label: "Closest finish" },
  form_jump: { emoji: "🚀", label: "Biggest form jump" },
  longest_session: { emoji: "⏳", label: "Longest together" },
  most_km: { emoji: "🛣️", label: "Most km in one go" },
  most_reps: { emoji: "🔁", label: "Most reps" },
  most_climb: { emoji: "⛰️", label: "Most climbing" },
  biggest_turnout: { emoji: "👥", label: "Biggest turnout" },
  latest_club_record: { emoji: "🏆", label: "Latest club record" },
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

// ---- motivation: company effect, badges, rivalry, goals, wrapped (docs/community.md "Motivation")
export interface CompanyPace { family: "short" | "medium" | "long"; label: string; diff_s_km: number; text: string; n_joint: number; n_solo: number; joint_s_km: number; solo_s_km: number;
  joint?: { gid: string; date: string; pace_s_km: number }[]; solo?: { activity_id: string; date: string; pace_s_km: number }[] }
export interface CompanyShort { diff: N; verdict: "company" | "alone" | "even" | null; text: string | null; n_joint: number; n_solo: number; n_compared: number;
  joint_avg: N; solo_avg: N; thin: boolean; pace: CompanyPace[] }
export interface Company extends CompanyShort {
  person: string; window_days: number;
  by_discipline: { discipline: string; n_joint: number; n_solo: number; joint_avg: N; solo_avg: N; diff: N }[];
  joint: { gid: string; date: string; delta: number; discipline: string; confidence?: string }[];
  solo: { activity_id: string; date: string; delta: number; discipline: string; confidence?: string; type?: string; name?: string }[];
}
export interface Badge { id: string; emoji: string; name: string; description: string; group: string; earned: boolean; date: string | null; gid: string | null;
  value: number; target: number; progress: number; unit: string; detail: string | null; person?: string }
export interface Badges { person: string; earned: number; total: number; badges: Badge[] }
export interface BadgesShort { earned: number; total: number; latest: { id: string; emoji: string; name: string; date: string; gid: string }[];
  next: { id: string; emoji: string; name: string; progress: number; value: number; target: number; unit: string } | null }
export interface RivalryPoint { gid: string; date: string; kind: "reps" | "race"; title: string; n: number; wins: Record<string, number>; ties: number;
  mean_gap_s: number; gap_s_km: N; closest_s: number; cum_wins: Record<string, number>; cum_ties: number }
export interface Rivalry { a: string; b: string; sessions: number; reps: number; wins: Record<string, number>; ties: number; mean_gap_s_km: N; mean_gap_s: N;
  leader: string | null; trend: { kind: "closing" | "pulling_away" | "steady" | "turned"; text: string; early: number; late: number; unit: string; leader_now: string; window: number } | null;
  timeline: RivalryPoint[]; headline: string }
export type GoalMetric = "sessions" | "km" | "hours" | "person_hours" | "reps" | "climb_m";
export interface Goal { id: string; title: string; metric: GoalMetric; target: number; from: string; to: string; default?: boolean; unit: string; metric_label: string;
  value: number; pct: number; expected: number; expected_pct: number; remaining: number; days: number; days_left: number;
  status: "done" | "ahead" | "on_track" | "behind" | "ended" | "upcoming"; text: string; n_sessions: number; last_gid: string | null;
  by_member: { person: string; value: number }[] }
export interface Goals { goal: Goal; goals: Goal[]; metrics: { id: GoalMetric; unit: string; label: string }[] }
export interface WrappedSession { gid: string; date: string; title: string; place: string; category: CatId; people: string[]; km: number; hours: number; climb_m: number; reps: number }
export interface WrappedMember {
  person: string; display: PersonInfo; sessions: number; share: number; km: number; hours: number; reps: number; climb_m: number; top_place: string | null;
  favourite_category: CatId | null; partner: { person: string; sessions: number } | null; best_session: { gid: string; date: string; title: string; delta: number } | null;
  avg_form_delta: N; records: number; badges: Badge[]; streak_weeks: number; company: CompanyShort;
  rivalry: { other: string; won: number; lost: number; ties: number; trend: string | null }[];
}
export interface WrappedTotals { sessions: number; km: number; hours: number; person_hours_together: number; reps: number; climb_m: number; kcal: number; members: number; places: number }
export interface Wrapped {
  year: number; years: string[]; people: Record<string, PersonInfo>; totals: WrappedTotals; previous: WrappedTotals | null;
  first: WrappedSession | null; last: WrappedSession | null;
  top_place: { name: string; sessions: number; others: { name: string; sessions: number }[] } | null;
  favourite_category: (Category & { mix: Category[] }) | null;
  months: { month: string; sessions: number; km: number }[]; busiest_month: { month: string; sessions: number; km: number } | null;
  biggest_day: WrappedSession | null; longest: (WrappedSession & { minutes: number }) | null;
  form_jump: (WrappedSession & { person: string; delta: number; text: string }) | null;
  closest_finish: Highlight | null; most_loyal: { person: string; sessions: number; of: number } | null; top_pair: { a: string; b: string; sessions: number } | null;
  records_set: { kind: string; text: string; person: string; gid: string; date: string }[]; records_standing: ClubRecord[];
  badges: Badge[]; rivalry: Pick<Rivalry, "a" | "b" | "sessions" | "reps" | "wins" | "ties" | "trend" | "headline">[];
  members: WrappedMember[];
}
/** "3:19" per km from seconds per km */
export const paceKm = (s: N | undefined) => (s === null || s === undefined ? "—" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
