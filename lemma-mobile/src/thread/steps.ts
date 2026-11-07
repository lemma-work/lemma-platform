import type { Note, RawMessage } from "@web/thread/turns";

/** A run's work, as the chat draws it: one quiet line while it goes, saying
 *  the step it is on, which folds into "Worked for 1m 6s · 5 steps" when it
 *  ends; that line opens the steps, each with its time.
 *
 *  Pure, so the tests read it directly. */

/** What a step says about itself worth reading: the agent's own sentence,
 *  or the command, path or address it is on. Not the names of its
 *  arguments, which is what a step with none of those falls back to
 *  (`argSummary`): "sql" under "Pod query" tells nobody anything. */
export function stepDetail(note: Pick<Note, "detail" | "said" | "kind">): string {
    const detail = note.detail.replace(/\s+/g, " ").trim();
    if (note.said || note.kind === "thought") return detail;
    if (!/^[a-z][a-z0-9_]*(, [a-z][a-z0-9_]*)*$/.test(detail)) return detail;
    /* One plain word may be what was searched for; several, a name with an
       underscore, or a usual argument's name is the arguments' names. */
    const names = detail.split(", ");
    return names.length > 1 || detail.includes("_") || ARGUMENT_NAMES.has(detail) ? "" : detail;
}

const ARGUMENT_NAMES = new Set(["sql", "cmd", "code", "prompt", "urls", "args", "arguments", "text", "data", "operation", "input", "edits", "action", "instructions", "reason", "questions", "title", "comment"]);

/** The step a run is on, as one line: the agent's own sentence when it gave
 *  one, else what the step is and what it is on. */
export function tickerLine(notes: readonly Note[], writing: boolean): string {
    if (writing) return "Writing";
    const last = notes[notes.length - 1];
    if (!last) return "Starting";
    if (last.kind === "thought") return "Thinking";
    const detail = stepDetail(last);
    if (last.said && detail) return detail;
    return detail ? last.label + " · " + detail : last.label;
}

export function stepCount(count: number): string {
    return count + " step" + (count === 1 ? "" : "s");
}

/** "Worked for 1m 6s · 5 steps", or "5 steps" when how long is not known. */
export function workedLine(span: string, count: number): string {
    return span ? "Worked for " + span + " · " + stepCount(count) : stepCount(count);
}

export interface StepTime {
    startMs?: number;
    endMs?: number;
}

function msOf(iso: string | undefined): number | undefined {
    if (!iso) return undefined;
    const at = Date.parse(iso);
    return Number.isNaN(at) ? undefined : at;
}

/** When each call started and when its return landed, by call id: what the
 *  steps list says each step took. */
export function stepTimes(messages: readonly RawMessage[]): ReadonlyMap<string, StepTime> {
    const times = new Map<string, StepTime>();
    for (const message of messages) {
        const id = message.tool_call_id;
        if (!id) continue;
        const at = msOf(message.created_at);
        const held = times.get(id) ?? {};
        if (message.kind === "TOOL_CALL") held.startMs = held.startMs ?? at;
        else if (message.kind === "TOOL_RETURN") held.endMs = at;
        times.set(id, held);
    }
    return times;
}

/** "3s", "1m 4s", "<1s"; empty when the step has not finished or was never
 *  timed. */
export function tookOf(time: StepTime | undefined): string {
    if (!time || time.startMs === undefined || time.endMs === undefined) return "";
    const seconds = (time.endMs - time.startMs) / 1000;
    if (seconds < 1) return "<1s";
    if (seconds < 60) return Math.round(seconds) + "s";
    const whole = Math.round(seconds);
    return Math.floor(whole / 60) + "m " + (whole % 60) + "s";
}
