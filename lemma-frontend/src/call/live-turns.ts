/** Turning a GPT-Live transcript back into a request.
 *
 *  Gemini hands the backend a request the model composed itself: the
 *  `message_agent` tool call arrives with clean text in it. GPT-Live does
 *  not. Its `session.delegation.created` carries an id and an offset and
 *  nothing else — the docs are explicit that the delegation object is
 *  metadata, not task text, and that working out what the person wants is
 *  the application's job.
 *
 *  So this is that job. Transcript deltas land here as they arrive, and when
 *  a delegation opens we cut the tape: everything said since the last cut is
 *  the request. The cut is what stops the second delegation from re-asking
 *  the first one's question. */

export type Speaker = "them" | "us";

export interface TranscriptDelta {
    speaker: Speaker;
    delta: string;
    startMs: number;
    endMs: number;
}

interface Fragment {
    speaker: Speaker;
    text: string;
    startMs: number;
    endMs: number;
}

export interface Cut {
    /** What the person said in this window — the request itself. */
    said: string;
    /** What the voice said in the same window, if anything. Context only:
     *  a request of "yes, do that" is unreadable without the question. */
    heard: string;
}

/* Fragment boundaries follow audio cadence, not sentences, so two fragments
   from the same speaker that touch in time are one thing being said. A pause
   longer than this is treated as a new line instead. */
const JOIN_GAP_MS = 600;

export class TranscriptLog {
    private readonly fragments: Fragment[] = [];
    private cutMs = 0;

    /** Merge a delta into the log. Fragments from one speaker that run
     *  together become a single line; the interleaving that full duplex
     *  produces is kept, because who spoke when is the only ordering the
     *  transcript has. */
    append({ speaker, delta, startMs, endMs }: TranscriptDelta): void {
        const text = delta.trim();
        if (!text) return;
        const last = this.fragments[this.fragments.length - 1];
        if (last && last.speaker === speaker && startMs - last.endMs <= JOIN_GAP_MS) {
            last.text = (last.text + " " + text).replace(/\s+/g, " ");
            last.endMs = Math.max(last.endMs, endMs);
            return;
        }
        this.fragments.push({ speaker, text, startMs, endMs });
    }

    /** The newest thing the given speaker said, whenever it was. */
    latest(speaker: Speaker): string {
        for (let i = this.fragments.length - 1; i >= 0; i--) {
            if (this.fragments[i].speaker === speaker) return this.fragments[i].text;
        }
        return "";
    }

    /** Everything since the last cut, and move the cut to `untilMs`.
     *
     *  Taking by time rather than by array position matters: transcript
     *  deltas can still be arriving for speech that ended before the
     *  delegation opened, and they get appended after it. The window is the
     *  clock's, not the queue's. */
    take(untilMs: number): Cut {
        const said: string[] = [];
        const heard: string[] = [];
        let reached = this.cutMs;
        for (const fragment of this.fragments) {
            if (fragment.endMs <= this.cutMs) continue;
            if (fragment.startMs > untilMs) continue;
            (fragment.speaker === "them" ? said : heard).push(fragment.text);
            reached = Math.max(reached, fragment.endMs);
        }
        this.cutMs = Math.max(untilMs, reached);
        return { said: said.join(" ").trim(), heard: heard.join(" ").trim() };
    }
}

/** A cut, written the way the teammate should receive it.
 *
 *  The person's own words go in as the message, because they are the
 *  message — this is not a paraphrase the way Gemini's tool call was. What
 *  the voice said meanwhile is bracketed above it, so "yes, that one" still
 *  has a referent by the time it reaches the conversation. */
export function requestFrom(cut: Cut): string {
    if (!cut.said) return "";
    if (!cut.heard) return cut.said;
    return `[On the call you had just said: "${cut.heard}"]\n${cut.said}`;
}

/* Every append event caps `content` at 500 tokens. Four characters to the
   token is the usual rough conversion, which would put the cap near 2000 —
   this sits well under it, because the cost of guessing high is a rejected
   append in the middle of an answer and the cost of guessing low is one more
   round trip. */
const APPEND_BUDGET = 1400;

/** Split an answer into appends that will fit.
 *
 *  Sentence boundaries first, because an append is injected as context the
 *  model may read aloud, and a chunk that ends mid-clause is a chunk it will
 *  read aloud mid-clause. Only text with no boundary to find gets cut by
 *  length. */
export function chunkForAppend(text: string, budget: number = APPEND_BUDGET): string[] {
    const clean = text.trim();
    if (!clean) return [];
    if (clean.length <= budget) return [clean];

    const pieces = clean.split(/(?<=[.!?])\s+|\n+/).filter(Boolean);
    const chunks: string[] = [];
    let current = "";
    for (const piece of pieces) {
        if (piece.length > budget) {
            if (current) { chunks.push(current); current = ""; }
            for (let i = 0; i < piece.length; i += budget) chunks.push(piece.slice(i, i + budget));
            continue;
        }
        if (!current) current = piece;
        else if (current.length + 1 + piece.length <= budget) current += " " + piece;
        else { chunks.push(current); current = piece; }
    }
    if (current) chunks.push(current);
    return chunks;
}
