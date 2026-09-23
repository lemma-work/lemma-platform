/** What to type, for somebody who does not know what they are allowed to ask.
 *
 *  The box on the hiring floor is the most open thing in the product and that
 *  is exactly its problem: "I need someone to…" is an invitation with no
 *  suggestion of scale, and the six cards underneath answer it by implying the
 *  answer has to be one of six. So the placeholder says a few of the things
 *  nobody would have guessed were allowed.
 *
 *  Deliberately *not* the six on the shelf. Repeating them would say the
 *  opposite of what this is for — the point of the sentence above the grid is
 *  that the grid is not the menu.
 *
 *  `typedAt` is a pure function of elapsed milliseconds rather than a state
 *  machine advanced by a timer, which is the whole reason it can be tested:
 *  the component owns a clock and nothing else, and every frame of this can be
 *  asked for by name. */

export const ASKS = [
    "manage our deal pipeline",
    "teach me something new every week",
    "answer the questions I keep answering",
    "watch for anyone writing about us",
    "remember what was decided on every call",
];

/* Typing is faster than a person and erasing is faster still, which is how a
   typewriter effect reads as a demonstration rather than as somebody slowly
   typing at you. The hold is the only part that matters for reading. */
const TYPE = 46;
const HOLD = 1700;
const ERASE = 22;
const GAP = 280;

function spanOf(phrase: string): number {
    return phrase.length * TYPE + HOLD + phrase.length * ERASE + GAP;
}

/** How much of the phrase list is showing `elapsed` ms in.
 *
 *  Empty during the gap between two phrases, which is what lets the caller put
 *  its own resting text back rather than leaving a field that flashes blank. */
export function typedAt(phrases: string[], elapsed: number): string {
    if (phrases.length === 0) return "";
    const whole = phrases.reduce((total, phrase) => total + spanOf(phrase), 0);
    /* A negative clock is a clock that has not started; a browser that
       suspended the tab and came back can produce one. */
    let at = elapsed <= 0 ? 0 : elapsed % whole;

    for (const phrase of phrases) {
        const span = spanOf(phrase);
        if (at >= span) { at -= span; continue; }

        const typing = phrase.length * TYPE;
        if (at < typing) return phrase.slice(0, Math.ceil(at / TYPE));
        if (at < typing + HOLD) return phrase;

        const erasing = at - typing - HOLD;
        if (erasing < phrase.length * ERASE) {
            return phrase.slice(0, phrase.length - Math.ceil(erasing / ERASE));
        }
        return "";
    }
    return "";
}
