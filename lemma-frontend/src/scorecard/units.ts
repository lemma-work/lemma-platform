/** What a teammate makes, read off its scorecard, and how a person's words
 *  are handed to it to become a measure. */

import type { MeasureHistory } from "./history";
import type { Measure } from "./measures";

export interface Unit {
    /** The work table: one row per unit. */
    table: string;
    /** What the unit is called, in the scorecard's own words. */
    label: string;
    /** Units in the last four weeks, or null while still counting. Taken from
     *  a share measure's totals, which count every row in each week. */
    count: number | null;
}

export function unitsOf(measures: Measure[], history: ReadonlyMap<string, MeasureHistory>): Unit[] {
    const units = new Map<string, Unit>();
    for (const measure of measures) {
        if (measure.counter !== "work" || !measure.unitTable) continue;
        const unit = units.get(measure.unitTable) ?? { table: measure.unitTable, label: measure.countedFrom || measure.unitTable, count: null };
        const weeks = history.get(measure.key)?.weeks;
        if (measure.shape === "share" && weeks && weeks.length > 0) {
            const total = weeks.reduce((sum, week) => sum + (week.total ?? 0), 0);
            unit.count = Math.max(unit.count ?? 0, total);
        }
        units.set(measure.unitTable, unit);
    }
    return [...units.values()];
}

/** The ask that goes to the teammate when a person says what good looks
 *  like. It puts the sentence in the composer, as every ask in the app does,
 *  and the person sends it, so it is written in their words and nothing of
 *  the machinery: how a draft is checked and filed is the teammate's
 *  `try_measure` tool's to say, not the person's message. */
/** The ask that goes to the teammate when one of its measures could not be
 *  counted: which one, and the counter's own reason, which is written for the
 *  teammate to act on rather than for the person to read. */
export function fixMeasure(measure: string, reason: string): string {
    return "Fix the goal “" + measure.replace(/[“”]/g, "\"") + "”: it could not be counted. " + reason.trim()
        + " Redraft it, count it over the last four weeks, and add it as a proposal for me to keep.";
}

export function wordsToMeasure(said: string): string {
    return "Add this to how you’re judged: “" + said.replace(/[“”]/g, "\"") + "”. "
        + "Turn it into a measure of your work, count it over the last four weeks, and add it as a proposal for me to keep.";
}
