// Types + helpers for /api/fitness (scripts/fitness_model.py -> fitness.json; docs/fitness-model.md).
import { cssVar, dur } from "./format";
import "../fitness.css";   // --disc-* tokens used by discColor()

/** what the app shows: one run number (`run`, the distance curve at 10 km) and ski */
export type Disc = "run" | "ski";
/** a session's evidence discipline (fitness.json sessions[].discipline): interval and long both feed the run curve */
export type SessDisc = "interval" | "long" | "ski";
export type Kind = "race" | "hard" | "bound" | "implausible";
export const DISCS: Disc[] = ["run", "ski"];
export const DISC_LABEL: Record<string, string> = { run: "Run fitness", ski: "Ski", overall: "Overall",
  interval: "Run · interval", long: "Run · distance" };
/** the shown discipline of a session / form delta discipline (interval, long -> run) */
export const discOf = (d: string): Disc => (d === "ski" ? "ski" : "run");
/** ?d= list -> shown disciplines; the old interval / long values (bookmarks) mean run */
export const parseDiscs = (v: string | null): Disc[] => {
  const want = new Set((v ?? "run,ski").split(",").map((d) => (d === "interval" || d === "long" ? "run" : d)));
  return DISCS.filter((d) => want.has(d));
};
/** own palette (tokens in fitness.css), distinct from the sport colours (run blue, ski red, hike green, bike ochre,
 *  swim teal): a discipline is a model quantity, not a sport. Running sessions (interval / long) take the run colour. */
const DISC_COLOR: Record<string, string> = { run: "--disc-run", interval: "--disc-run", long: "--disc-run", ski: "--disc-ski", overall: "--disc-overall" };
export const discColor = (d: string) => cssVar(DISC_COLOR[d] ?? "--chart-muted");
export const KIND_LABEL: Record<Kind, string> = { race: "race", hard: "hard session", bound: "lower bound", implausible: "implausible" };
export const KIND_HELP: Record<Kind, string> = {
  race: "a race: full weight up to 2.5 sd either side",
  hard: "a hard session: full weight up to 1 sd below / 2.5 sd above the shape (sessions are often not all-out)",
  bound: "a lower bound (easy / long session): ignored when below the shape, counted only when clearly above",
  implausible: "rejected: faster than your shape allows (mislabelled or bad GPS)",
};

type RaceEq = { "5k": number; "10k": number; half: number; marathon: number };
interface Weekly { date: string; mean: number; sd: number; n?: number; days_since_evidence?: number | null }
export interface Yearly { mean: number; max: number; sd_median: number; n_evidence: number; mean_well_observed: number | null; days_well_observed: number }
interface Current { date: string; mean: number; sd: number; sd_state?: number; confidence?: "high" | "medium" | "low"; pct_of_best?: number;
  race_equivalents_s?: RaceEq | null; trend_90d: number | null;
  last_evidence?: { date: string; id: number; score: number; kind: Kind } | null }
interface Discipline { label: string; unit: string; q_per_day?: number; personal_best?: number; current: Current;
  yearly: Record<string, Yearly | number>; weekly: Weekly[] }
export interface FitSession { id: string; date: string; discipline: SessDisc; type: string; score: number; kind: Kind; sigma: number;
  /** running: the curve at the session's own distance that day; score_10k = the score carried along that curve to 10 km */
  weight: number | null; shape_at: number | null; score_10k?: number | null; km?: number | null; confidence: number; components: Record<string, any>; explain: string;
  name?: string | null; dist_km?: number | null; sport?: string | null; hr?: SessionHr | null }
/** the session as seen by the "hr" model variant (HR decides how maximal a hard session was) */
interface SessionHr { kind: Kind; sigma: number; weight: number | null; shape_at: number | null; p_max: number | null; note: string | null }
type VariantName = "pace" | "hr";
interface Variant { label: string; hr_used_in_score: boolean; disciplines?: Record<string, Discipline> }
export interface EvalDisc { n: number; forecast_mae: number; loglik: number; race_n?: number; race_mae?: number; race_mean_err?: number;
  r3_n?: number; r3_corr_prior_shape_vs_log_speed?: number }
interface EvalVariants { default: Record<VariantName, Record<SessDisc, EvalDisc>>;
  demoted: { date: string; id: string; discipline: SessDisc; type: string; score: number; p_max: number; note: string }[];
  sensitivity_hr?: Record<string, Record<SessDisc, Partial<EvalDisc>>> }
/** fitness.json `races` (official results; lib/races RaceRow without the field) */
export type FitRace = Omit<import("./races").RaceRow, "field">;
export interface FitnessDoc { built: string; model_version: number; params: Record<string, any>; sigma: Record<string, number>;
  scale: string; hr_used_in_score: boolean; disciplines: Record<string, Discipline>; sessions: FitSession[];
  variants?: Partial<Record<VariantName, Variant>>; races?: FitRace[]; eval_variants?: EvalVariants | null;
  rebuild: { running: boolean; started: string | null; finished: string | null; error: string | null; seconds: number | null } }
interface Explained { key: string; label: string; value?: any; text: string; meaning: string }
interface SessionShape extends FitSession { label: string; race_equivalents_s: RaceEq | null; components_explained: Explained[];
  shape: { date: string; mean: number; sd: number; km?: number } | null; shape_race_equivalents_s: RaceEq | null;
  personal_best: number | null; current: Current | null;
  hr?: (SessionHr & { shape: { date: string; mean: number; sd: number } | null; current: Current | null; label: string | null }) | null }
export interface FitnessSession { id: string; scored: boolean; sessions: SessionShape[]; hr_used_in_score: boolean; built: string;
  hr_diagnostics: ({ n_chunks: number; vdot?: number; iqr?: number; hrr?: number; ef?: number; decoupling_pct?: number; explained: Explained[] }) | null }

export const pm = (m: number | null | undefined, sd: number | null | undefined, d = 1) =>
  m === null || m === undefined ? "—" : sd === null || sd === undefined ? m.toFixed(d) : `${m.toFixed(d)} ± ${sd.toFixed(d)}`;
export const signed = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(v).toFixed(d));
export const raceT = (s: number | null | undefined) => (s ? dur(s) : "—");
export const unitShort = (d: string) => (d === "ski" ? "VDOT-eq." : d === "overall" ? "% of best" : "VDOT");
export const daysAgo = (date: string, ref: string) => Math.round((Date.parse(ref) - Date.parse(date)) / 86400000);
