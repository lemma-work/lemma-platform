"use client";

import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { isPodDefaultAgent } from "@/data/agent-names";
import type { StandingJob } from "@/schedule/schedules";
import {
    BASIC_MEASURES,
    SCORECARD_COLUMNS,
    SCORECARD_TABLE,
    WEEKS_TABLE,
    byPosition,
    measureRow,
    readMeasure,
    readWeekResult,
    type Measure,
    type WeekResult,
} from "./measures";
import { SUGGESTIONS_TABLE, SUGGESTION_COLUMNS, readSuggestion, type Suggestion, type SuggestionStatus } from "./suggestions";
import { REVIEW_SCHEDULE } from "./review";
import { readPreview, readRows, type Preview, type UnitRow } from "./history";

/** Reads and writes of a teammate's scorecard tables.
 *
 *  Straight to the records API, the way page comments are: these are ordinary
 *  tables in the space, and the data source's table reads are built for
 *  browsing pages of rows, not for a component that needs every row of a
 *  short table. The sample workspace has no scorecard, and says so as
 *  "missing" rather than failing. */

const missing = (error: unknown) => (error as { statusCode?: number } | null)?.statusCode === 404;
const live = () => source.label !== "sample";

function items(listed: unknown): unknown[] {
    return (listed as { items?: unknown[] } | null)?.items ?? [];
}

/** The scorecard's measures, or null when the space has no scorecard. */
export async function readScorecard(podId: string): Promise<Measure[] | null> {
    if (!live()) return null;
    try {
        const listed = await lemma(podId).records.list(SCORECARD_TABLE, { limit: 100 });
        return byPosition(items(listed).map(readMeasure).filter((one): one is Measure => one !== null));
    } catch (error) {
        if (missing(error)) return null;
        throw error;
    }
}

export async function updateMeasure(podId: string, id: string, patch: { is_on?: boolean; target?: number; target_label?: string; proposed_from?: null }): Promise<void> {
    await lemma(podId).records.update(SCORECARD_TABLE, id, patch);
}

/** A proposal nobody wanted: the row goes, so the next review does not see it. */
export async function dropMeasure(podId: string, id: string): Promise<void> {
    await lemma(podId).records.delete(SCORECARD_TABLE, id);
}

/** Every measure's last four weeks, counted by the server the way the Friday
 *  review counts them, without recording anything. */
export async function previewScorecard(podId: string): Promise<Preview> {
    if (!live()) return { windows: [], measures: [] };
    return readPreview(await lemma(podId).request("POST", "/pods/" + podId + "/scorecard/preview", { body: { weeks: 4 } }));
}

/** The units behind one measure's number for the week ending `end`. */
export async function measureRows(podId: string, key: string, end?: string): Promise<UnitRow[]> {
    const query = end ? "?end=" + encodeURIComponent(end) : "";
    return readRows(await lemma(podId).request("GET", "/pods/" + podId + "/scorecard/measures/" + encodeURIComponent(key) + "/rows" + query));
}

/** A scorecard for a teammate hired without a role: the table, and the
 *  measures any job can be counted on. */
export async function startScorecard(podId: string): Promise<void> {
    if (!live()) throw new Error("The sample workspace has no tables to add a scorecard to.");
    await lemma(podId).tables.create({
        name: SCORECARD_TABLE,
        columns: SCORECARD_COLUMNS,
        enable_rls: false,
        config: { description: "How this teammate is judged: one row per measure, counted by the weekly review." },
    } as never);
    for (const measure of BASIC_MEASURES) await lemma(podId).records.create(SCORECARD_TABLE, measureRow(measure));
}

/** The table the review writes its suggestions into, made before the first
 *  review so the review never has to make a table of its own. */
export async function ensureSuggestionsTable(podId: string): Promise<void> {
    try {
        await lemma(podId).tables.get(SUGGESTIONS_TABLE);
    } catch (error) {
        if (!missing(error)) throw error;
        await lemma(podId).tables.create({
            name: SUGGESTIONS_TABLE,
            columns: SUGGESTION_COLUMNS,
            enable_rls: false,
            config: { description: "Changes the weekly review suggests. A person adds or dismisses each one." },
        } as never);
    }
}

export async function readWeeks(podId: string): Promise<WeekResult[]> {
    if (!live()) return [];
    try {
        const listed = await lemma(podId).records.list(WEEKS_TABLE, { sort: [{ field: "week", direction: "desc" }], limit: 100 });
        return items(listed).map(readWeekResult).filter((one): one is WeekResult => one !== null);
    } catch (error) {
        if (missing(error)) return [];
        throw error;
    }
}

export async function readOpenSuggestions(podId: string): Promise<Suggestion[]> {
    if (!live()) return [];
    try {
        const listed = await lemma(podId).records.list(SUGGESTIONS_TABLE, {
            filters: [{ field: "status", op: "eq", value: "open" }],
            sort: [{ field: "created_at", direction: "asc" }],
            limit: 20,
        });
        return items(listed).map(readSuggestion).filter((one): one is Suggestion => one !== null && one.status === "open");
    } catch (error) {
        if (missing(error)) return [];
        throw error;
    }
}

export async function markSuggestion(podId: string, id: string, status: SuggestionStatus): Promise<void> {
    await lemma(podId).records.update(SUGGESTIONS_TABLE, id, { status });
}

/** The Friday review among a space's standing work, if it is turned on. */
export function reviewSchedule(jobs: StandingJob[] | undefined): StandingJob | null {
    return (jobs ?? []).find((job) => job.name === REVIEW_SCHEDULE && job.target.kind === "agent" && isPodDefaultAgent(job.target.name)) ?? null;
}

export const scorecardKey = (podId: string) => ["scorecard", podId] as const;
export const weeksKey = (podId: string) => ["scorecard-weeks", podId] as const;
export const suggestionsKey = (podId: string) => ["review-suggestions", podId] as const;

export const previewKey = (podId: string) => ["scorecard-preview", podId] as const;

export function usePreview(podId: string, enabled: boolean) {
    return useQuery({ queryKey: previewKey(podId), queryFn: () => previewScorecard(podId), enabled, staleTime: 5 * 60_000 });
}

export function useScorecard(podId: string) {
    return useQuery({ queryKey: scorecardKey(podId), queryFn: () => readScorecard(podId), staleTime: 60_000 });
}

/** The space's standing work, under the key the Standing work section uses,
 *  so the two never disagree about whether the review is on. */
export function useStandingWork(podId: string) {
    return useQuery({ queryKey: ["schedules", podId], queryFn: () => source.listSchedules(podId), staleTime: 60_000 });
}

/** Where a teammate is in being set up to be judged.
 *
 *  - `none`: no scorecard, and nothing says one is coming.
 *  - `choosing`: a scorecard is here — a role brought one — and the review is
 *    not on yet. This is the one state the sidebar and Home mention.
 *  - `reviewed`: the Friday review is on.
 *  - `unknown`: still reading, or the reads failed. Nothing is said. */
export type SetupState = "none" | "choosing" | "reviewed" | "unknown";

export function useSetupState(podId: string): SetupState {
    const card = useScorecard(podId);
    const jobs = useStandingWork(podId);
    if (!card.isSuccess || !jobs.isSuccess) return "unknown";
    if (reviewSchedule(jobs.data)) return "reviewed";
    return card.data ? "choosing" : "none";
}
