/** A measure's last few weeks, as the preview endpoint counts them, and the
 *  bars that draw them.
 *
 *  Nothing here counts. The server scores every window the same way the
 *  Friday review does; this reads its answer and decides how tall each bar is
 *  and whether it is drawn as met, missed or not judged. */

import type { Measure } from "./measures";

export interface WeekScore {
    status: string;
    shown: string;
    counted: number | null;
    total: number | null;
    value: number | null;
    met: boolean | null;
    /** Why the week could not be counted, in the counter's words. */
    reason?: string | null;
}

export interface MeasureHistory {
    key: string;
    weeks: WeekScore[];
}

export interface Preview {
    windows: { start: string; end: string }[];
    measures: MeasureHistory[];
}

export interface UnitRow {
    id: string;
    label: string;
    link: string | null;
    at: string | null;
    passed: boolean | null;
}

function num(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string {
    return typeof value === "string" ? value : "";
}

function readWeek(raw: unknown): WeekScore {
    const row = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    return {
        status: str(row.status),
        shown: str(row.shown),
        counted: num(row.counted),
        total: num(row.total),
        value: num(row.value),
        met: typeof row.met === "boolean" ? row.met : null,
        reason: str(row.reason) || null,
    };
}

export function readPreview(raw: unknown): Preview {
    const body = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const windows = Array.isArray(body.windows)
        ? body.windows.map((one) => {
            const w = (one ?? {}) as Record<string, unknown>;
            return { start: str(w.start), end: str(w.end) };
        })
        : [];
    const measures = Array.isArray(body.measures)
        ? body.measures
            .map((one) => {
                const m = (one ?? {}) as Record<string, unknown>;
                return { key: str(m.key), weeks: Array.isArray(m.weeks) ? m.weeks.map(readWeek) : [] };
            })
            .filter((one) => one.key)
        : [];
    return { windows, measures };
}

export function readRows(raw: unknown): UnitRow[] {
    const body = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    if (!Array.isArray(body.rows)) return [];
    return body.rows.map((one) => {
        const row = (one ?? {}) as Record<string, unknown>;
        return {
            id: str(row.id),
            label: str(row.label) || "Untitled",
            link: str(row.link) || null,
            at: str(row.at) || null,
            passed: typeof row.passed === "boolean" ? row.passed : null,
        };
    });
}

export interface Bar {
    /** 0–1 of the drawing's height. */
    height: number;
    state: "met" | "missed" | "none";
}

/** The bars for a measure's weeks, and where its target line sits.
 *
 *  A share is drawn on its own scale, 0 to every one, so 9 in 10 always sits
 *  at the same height. A count or a median has no natural top, so the scale
 *  is the largest of the weeks and the target — a week of zero still draws a
 *  sliver, because "none" is a result and an empty slot reads as missing. */
export function barsFor(measure: Pick<Measure, "shape" | "target">, weeks: WeekScore[]): { bars: Bar[]; target: number } {
    const state = (week: WeekScore): Bar["state"] => (week.met === null ? "none" : week.met ? "met" : "missed");
    if (measure.shape === "share") {
        return {
            bars: weeks.map((week) => ({ height: clamp(week.value ?? 0), state: state(week) })),
            target: clamp(measure.target),
        };
    }
    const values = weeks.map((week) => week.value ?? 0);
    const top = Math.max(...values, measure.target, 1);
    return {
        bars: weeks.map((week, at) => ({ height: Math.max(0.06, values.at(at)! / top), state: state(week) })),
        target: measure.target / top,
    };
}

function clamp(value: number): number {
    return Math.min(1, Math.max(0, value));
}

/** What the newest week said, for the line beside the bars. */
export function lastWeek(weeks: WeekScore[]): string {
    const last = weeks.at(-1);
    if (!last) return "";
    return last.shown || (last.status === "nothing_to_count" ? "nothing to count" : "");
}
