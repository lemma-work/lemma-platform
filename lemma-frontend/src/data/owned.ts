/** What a Lemma owns, as the API keeps it on the pod (`config.outcome`), and
 *  where its number stands. One read for every member: the measure counts
 *  every row of the tables it names, so nobody's number depends on which rows
 *  they may open. */

export interface OwnedInput {
    /** "Every customer hears back within a day." */
    sentence: string;
    /** A bar held every week, or a finish line by a date. */
    kind: "week" | "date";
    target: number | null;
    /** `%` when the measure answers a share, 0 to 100. */
    unit: string | null;
    /** `YYYY-MM-DD`, for a finish line only. */
    due: string | null;
    /** One read-only query over the pod's tables that answers one number. */
    measure: string | null;
}

export interface Owned extends OwnedInput {
    /** What the measure answered when last read; null with no measure or when
     *  it could not be read. */
    value: number | null;
    /** Why there is no value, in plain words. */
    problem: string | null;
}

function text(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value : null;
}

function number(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** `GET /pods/{id}/outcome/standing`, or null for a pod that owns nothing. */
export function readOwned(raw: unknown): Owned | null {
    const body = (raw ?? {}) as { outcome?: Record<string, unknown> | null; value?: unknown; problem?: unknown };
    const outcome = body.outcome;
    const sentence = text(outcome?.sentence);
    if (!outcome || !sentence) return null;
    return {
        sentence,
        kind: outcome.kind === "date" ? "date" : "week",
        target: number(outcome.target),
        unit: text(outcome.unit),
        due: text(outcome.due),
        measure: text(outcome.measure),
        value: number(body.value),
        problem: text(body.problem),
    };
}
