/** A reply drawn word by word as it streams, rather than in the jumpy
 *  chunks the stream happens to arrive in.
 *
 *  The words already on screen stay; new ones are let in a few a tick, so a
 *  big chunk lands as a quick run of words and a slow stream as a steady
 *  one, and each word fades in over a few ticks after it arrives. A word
 *  still being written (no space after it yet) waits until it is whole.
 *
 *  Pure, so the tests read it directly: `stream-speech.tsx` draws it. */

/** One step of the reveal. */
export const TICK_MS = 32;
/** However far behind, caught up within about this many ticks. */
export const CATCH_UP_TICKS = 8;
/** How many ticks a word takes to fade in. */
export const FADE_TICKS = 6;
/** Where a word starts its fade. */
export const FADE_FROM = 0.18;

const WORD = /\S+/g;

/** The words that are whole: all of them once the stream is over, all but a
 *  last one still being written while it goes. */
export function wholeWords(text: string, live: boolean): number {
    const count = text.match(WORD)?.length ?? 0;
    if (!live || count === 0 || /\s$/.test(text)) return count;
    return count - 1;
}

/** How many words to show after the next tick. */
export function nextShown(shown: number, available: number): number {
    if (shown >= available) return available;
    const behind = available - shown;
    return Math.min(available, shown + Math.max(1, Math.ceil(behind / CATCH_UP_TICKS)));
}

/** The text up to the end of its `count`th word, with the space after it
 *  left off. */
export function wordsUpTo(text: string, count: number): string {
    if (count <= 0) return "";
    WORD.lastIndex = 0;
    let seen = 0;
    let end = 0;
    for (let match = WORD.exec(text); match; match = WORD.exec(text)) {
        seen += 1;
        end = match.index + match[0].length;
        if (seen === count) break;
    }
    WORD.lastIndex = 0;
    return text.slice(0, end);
}

/** How opaque a word is, by how many ticks ago it was let in. */
export function fadeOf(ageTicks: number): number {
    if (ageTicks >= FADE_TICKS) return 1;
    const at = Math.max(0, ageTicks) / FADE_TICKS;
    return FADE_FROM + (1 - FADE_FROM) * at;
}

/** When the newest words were let in, oldest first: `[tick, how many]`, for
 *  the last few reveals only. */
export type Arrivals = readonly (readonly [tick: number, count: number])[];

/** How long ago the word `fromEnd` places from the end arrived. */
export function ageFromEnd(fromEnd: number, arrivals: Arrivals, now: number): number {
    let passed = 0;
    for (let index = arrivals.length - 1; index >= 0; index -= 1) {
        const [tick, count] = arrivals[index];
        passed += count;
        if (fromEnd < passed) return now - tick;
    }
    return FADE_TICKS;
}

/** Record a reveal, keeping only what can still be fading. */
export function arrived(arrivals: Arrivals, tick: number, count: number): Arrivals {
    if (count <= 0) return arrivals;
    return [...arrivals.filter(([at]) => tick - at < FADE_TICKS), [tick, count] as const];
}

/** Whether anything is still fading. */
export function stillFading(arrivals: Arrivals, now: number): boolean {
    return arrivals.some(([at]) => now - at < FADE_TICKS);
}

/** Where the reply splits: everything before the last paragraph break,
 *  which is drawn as it will stay, and the paragraph being written, which
 *  fades. Never inside a fenced code block, which is one piece. */
export function splitTail(text: string): { settled: string; tail: string } {
    let fenced = false;
    let cut = 0;
    let offset = 0;
    const lines = text.split("\n");
    for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
        offset += line.length + 1;
        /* A blank line inside a fence is part of the code; one outside it
           ends a paragraph. */
        if (!fenced && line.trim() === "" && index > 0 && index < lines.length - 1) cut = offset;
    }
    return { settled: text.slice(0, cut), tail: text.slice(cut) };
}
