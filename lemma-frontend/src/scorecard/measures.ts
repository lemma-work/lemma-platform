/** How a teammate is judged, as the app reads it.
 *
 *  A scorecard is an ordinary table in the teammate's space, `scorecard`, one
 *  row per measure. A role's template arrives with one filled in; a teammate
 *  hired without a role starts from `BASIC_MEASURES`. The weekly review counts
 *  every row that is on — in code, through the `score_week` tool — and writes
 *  the results to `scorecard_weeks`. The teammate never scores itself: it only
 *  writes the summary and suggests changes.
 *
 *  Plain tables rather than a typed object, so the person can open them like
 *  any other table, and a template can carry them like any other table. The
 *  columns here are the contract with the templates' `scorecard.json` and with
 *  `score_week`; a test holds them to the template files. */

export const SCORECARD_TABLE = "scorecard";
export const WEEKS_TABLE = "scorecard_weeks";

export type MeasureKind = "outcome" | "verdict" | "reliability";
export type Aim = "higher" | "lower";
/** share: units that passed the test, out of all. count: units that passed.
 *  median: the middle value over units that passed. total: their values
 *  added up — reach, views, revenue. */
export type Shape = "share" | "count" | "median" | "total";

export interface Measure {
    id: string;
    key: string;
    /** The measure in a sentence: "Callbacks happen by their date". */
    measure: string;
    kind: MeasureKind;
    /** What counts it: `approvals`, `standing_work`, `open_questions`, `sql`,
     *  or a counter that does not exist yet. */
    counter: string;
    query: string | null;
    /** `higher`: a share of the week that should reach the target. `lower`: a
     *  count that should stay at or under it. */
    aim: Aim;
    target: number;
    targetLabel: string;
    /** Where the number comes from, in the reader's words. */
    countedFrom: string;
    on: boolean;
    /** Why a measure is off, when the template or the app knows. */
    note: string | null;
    position: number;
    shape: Shape;
    /** For a `work` measure: the table holding one row per unit of work, the
     *  column that places a row in a week, and the test each row passes. */
    unitTable: string | null;
    timeColumn: string | null;
    test: string | null;
    /** The unit a median or a count is said in: "days", "views". */
    valueUnit: string | null;
    /** The person's words, while this row is a proposal nobody has kept. */
    proposedFrom: string | null;
}

/** The counters `score_week` can count. A template may name others — a job's
 *  real outcome often lives in a tool nothing counts yet — and those are shown
 *  off, with the reason, rather than left out: the person should see what the
 *  role would be judged on, and that it is not counted yet. */
export const COUNTED_BY_THE_REVIEW: ReadonlySet<string> = new Set(["approvals", "standing_work", "open_questions", "sql", "work"]);

export function countable(measure: Pick<Measure, "counter">): boolean {
    return COUNTED_BY_THE_REVIEW.has(measure.counter);
}

const KINDS: readonly MeasureKind[] = ["outcome", "verdict", "reliability"];

function text(value: unknown): string {
    return typeof value === "string" ? value : "";
}

function number(value: unknown): number | null {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
    return null;
}

function bool(value: unknown): boolean {
    return value === true || value === "true" || value === 1;
}

/** One row of `scorecard`, or null when it is not one the app can show. */
export function readMeasure(row: unknown): Measure | null {
    if (!row || typeof row !== "object") return null;
    const raw = row as Record<string, unknown>;
    const id = text(raw.id);
    const key = text(raw.key);
    const measure = text(raw.measure);
    const target = number(raw.target);
    if (!id || !key || !measure || target === null) return null;
    const kind = KINDS.includes(raw.kind as MeasureKind) ? (raw.kind as MeasureKind) : "outcome";
    return {
        id,
        key,
        measure,
        kind,
        counter: text(raw.counter),
        query: text(raw.query) || null,
        aim: raw.aim === "lower" ? "lower" : "higher",
        target,
        targetLabel: text(raw.target_label) || sayTarget(shapeOf(raw), raw.aim === "lower" ? "lower" : "higher", target, text(raw.value_unit) || null),
        countedFrom: text(raw.counted_from),
        on: bool(raw.is_on),
        note: text(raw.note) || null,
        position: number(raw.position) ?? 0,
        shape: shapeOf(raw),
        unitTable: text(raw.unit_table) || null,
        timeColumn: text(raw.time_column) || null,
        test: text(raw.test) || null,
        valueUnit: text(raw.value_unit) || null,
        proposedFrom: text(raw.proposed_from) || null,
    };
}

/** Checked for every teammate and said as one line under the scorecard, not
 *  as rows: whether standing work ran and whether anything was left waiting
 *  are true of any job, so as rows they made every scorecard look the same. */
export function alwaysChecked(measure: Pick<Measure, "kind">): boolean {
    return measure.kind === "reliability";
}

/** A row the teammate drafted from somebody's words, not yet kept. */
export function isProposal(measure: Pick<Measure, "proposedFrom" | "on">): boolean {
    return Boolean(measure.proposedFrom) && !measure.on;
}

/** Whether a number of this measure opens to the rows behind it. */
export function hasRows(measure: Pick<Measure, "counter">): boolean {
    return measure.counter === "work";
}

export function byPosition(measures: Measure[]): Measure[] {
    return [...measures].sort((a, b) => a.position - b.position || a.measure.localeCompare(b.measure));
}

const SHARES: Record<number, string> = { 1: "every one", 0.9: "9 in 10", 0.75: "3 in 4", 0.5: "half" };

/** A target in words, for a row written without its own `target_label`.
 *
 *  There is no picker for targets. A list of fractions was only ever right for
 *  a share, and it was offered on a median of minutes and a total of views
 *  alike; a person changes a target by saying so, and the teammate redrafts
 *  the measure, which counts it before anyone keeps it. */
export function sayTarget(shape: Shape, aim: Aim, target: number, unit: string | null = null): string {
    if (shape === "share") return SHARES[target] ?? Math.round(target * 100) + "%";
    if (aim === "lower" && target === 0) return "none";
    const amount = target.toLocaleString("en-US") + (unit ? " " + unit : "");
    return (aim === "lower" ? "at most " : "at least ") + amount;
}

function shapeOf(raw: Record<string, unknown>): Shape {
    const said = raw.shape;
    if (said === "share" || said === "count" || said === "median" || said === "total") return said;
    return raw.aim === "lower" ? "count" : "share";
}

/** Why a measure cannot be turned on, or null when it can. */
export function whyOff(measure: Measure): string | null {
    if (!countable(measure)) return measure.note || "Not counted yet";
    if (measure.counter === "sql" && !measure.query) return "Has no query to count with";
    if (measure.counter === "work" && (!measure.unitTable || !measure.timeColumn)) return "Says nothing about what to count";
    return null;
}

/** What a teammate hired without a role starts with: only the checks every
 *  teammate gets. What it is judged on comes from what it makes, which nobody
 *  has said yet — so the scorecard opens on that question, not on rows. */
export const BASIC_MEASURES: Omit<Measure, "id">[] = [
    {
        key: "standing_work", measure: "Standing work runs on time", kind: "reliability", counter: "standing_work", query: null,
        aim: "higher", target: 1, targetLabel: "every run", countedFrom: "Run history", on: true, note: null, position: 90,
        shape: "share", unitTable: null, timeColumn: null, test: null, valueUnit: null, proposedFrom: null,
    },
    {
        key: "open_questions", measure: "Nothing waits on a question for a day", kind: "reliability", counter: "open_questions", query: null,
        aim: "lower", target: 0, targetLabel: "none", countedFrom: "Conversations", on: true, note: null, position: 91,
        shape: "count", unitTable: null, timeColumn: null, test: null, valueUnit: null, proposedFrom: null,
    },
];

/** A measure as a row of the table. */
export function measureRow(measure: Omit<Measure, "id">): Record<string, unknown> {
    return {
        key: measure.key,
        measure: measure.measure,
        kind: measure.kind,
        counter: measure.counter,
        query: measure.query,
        aim: measure.aim,
        target: measure.target,
        target_label: measure.targetLabel,
        counted_from: measure.countedFrom,
        is_on: measure.on,
        note: measure.note,
        position: measure.position,
        shape: measure.shape,
    };
}

/** The table's columns, for a teammate without a template. The same shape as
 *  the templates' `tables/scorecard/scorecard.json`. */
export const SCORECARD_COLUMNS = [
    { name: "key", type: "TEXT", required: true, unique: true },
    { name: "measure", type: "TEXT", required: true },
    { name: "kind", type: "TEXT", required: true },
    { name: "counter", type: "TEXT", required: true },
    { name: "query", type: "TEXT", required: false },
    { name: "aim", type: "TEXT", required: true },
    { name: "target", type: "FLOAT", required: true },
    { name: "target_label", type: "TEXT", required: true },
    { name: "counted_from", type: "TEXT", required: true },
    { name: "is_on", type: "BOOLEAN", required: true },
    { name: "note", type: "TEXT", required: false },
    { name: "position", type: "INTEGER", required: true },
    { name: "shape", type: "TEXT", required: false },
    { name: "unit_table", type: "TEXT", required: false },
    { name: "time_column", type: "TEXT", required: false },
    { name: "test", type: "TEXT", required: false },
    { name: "value", type: "TEXT", required: false },
    { name: "value_unit", type: "TEXT", required: false },
    { name: "label_column", type: "TEXT", required: false },
    { name: "link_column", type: "TEXT", required: false },
    { name: "proposed_from", type: "TEXT", required: false },
] as const;

/** One measure's result for one week, as `score_week` wrote it. */
export interface WeekResult {
    week: string;
    key: string;
    measure: string;
    shown: string;
    met: boolean | null;
    status: string;
    targetLabel: string;
}

export function readWeekResult(row: unknown): WeekResult | null {
    if (!row || typeof row !== "object") return null;
    const raw = row as Record<string, unknown>;
    const week = text(raw.week).slice(0, 10);
    const key = text(raw.key);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(week) || !key) return null;
    return {
        week,
        key,
        measure: text(raw.measure) || key,
        shown: text(raw.shown),
        met: raw.met === null || raw.met === undefined ? null : bool(raw.met),
        status: text(raw.status),
        targetLabel: text(raw.target_label),
    };
}

/** The newest week's results, in the scorecard's order. */
export function latestWeek(results: WeekResult[], measures: Measure[]): { week: string; results: WeekResult[] } | null {
    if (results.length === 0) return null;
    const week = results.map((one) => one.week).sort().at(-1)!;
    const order = new Map(byPosition(measures).map((one, at) => [one.key, at]));
    const those = results
        .filter((one) => one.week === week)
        .sort((a, b) => (order.get(a.key) ?? 99) - (order.get(b.key) ?? 99));
    return { week, results: those };
}

/** "2 of 3 on target" — counted over the measures the week could judge. */
export function sayMet(results: WeekResult[]): string {
    const judged = results.filter((one) => one.met !== null);
    if (judged.length === 0) return "Nothing to count yet";
    return judged.filter((one) => one.met).length + " of " + judged.length + " on target";
}
