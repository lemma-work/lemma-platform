import type { Speaker } from "./live-turns";

/** One line of a call, as it is being said.
 *
 *  `done` is the difference between a line that is finished and a line still
 *  arriving — both providers stream transcription in fragments, and a line
 *  that looks finished because nothing has arrived for a moment is not the
 *  same as one the provider has closed. */
export interface CallLine {
    id: number;
    speaker: Speaker;
    text: string;
    done: boolean;
}

/** Captions show the last line or two; nothing reads further back than that.
 *  A handful more are kept only so the tail is never the whole array, and the
 *  record of what happened is the conversation itself, on the server. */
const MAX_LINES = 24;

/** Fold one transcription fragment into the lines so far.
 *
 *  Pure, and returns a new array, so React sees the change and the whole
 *  thing can be tested without a browser or a provider.
 *
 *  The joining rule is the only decision here: fragments from one speaker run
 *  together into a line until that speaker's line is closed. Fragments do not
 *  arrive one per sentence — they arrive at whatever rhythm the recogniser
 *  emits, often mid-word — so a line per fragment would be a transcript one
 *  syllable wide. */
export function foldTranscript(
    lines: CallLine[],
    chunk: { speaker: Speaker; text: string; final?: boolean },
): CallLine[] {
    const text = chunk.text ?? "";
    const last = lines[lines.length - 1];
    const open = last && last.speaker === chunk.speaker && !last.done ? last : null;

    /* A close with nothing in it closes the open line and adds none. This is
       how a provider says "that speaker has stopped", and treating it as an
       empty line would put a blank row on screen every time somebody paused. */
    if (!text.trim()) {
        if (!open || !chunk.final) return lines;
        return [...lines.slice(0, -1), { ...open, done: true }];
    }

    if (open) {
        const joined = (open.text + text).replace(/\s+/g, " ").trimStart();
        return [...lines.slice(0, -1), { ...open, text: joined, done: Boolean(chunk.final) }];
    }

    const next = [
        ...lines,
        {
            /* Monotonic from the line before it rather than an index, so a line
               keeps its React key when the head of the list is trimmed away. */
            id: (last?.id ?? 0) + 1,
            speaker: chunk.speaker,
            text: text.replace(/\s+/g, " ").trimStart(),
            done: Boolean(chunk.final),
        },
    ];
    return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
}

export function isSameUtterance(a: string, b: string): boolean {
    const flatten = (text: string) => text.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
    const left = flatten(a);
    return Boolean(left) && left === flatten(b);
}
