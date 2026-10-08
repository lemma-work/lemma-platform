/** A reply still streaming, cut into the blocks that are finished and the one
 *  still being written.
 *
 *  Streaming text only ever grows at its end, so a block with a blank line and
 *  another block after it will never change again. Rendering those once and
 *  re-parsing only the tail is what keeps a long answer from re-running the
 *  whole markdown pipeline — GFM, raw HTML, the sanitiser — for every few
 *  tokens, which made the cost of each update grow with everything already
 *  said.
 *
 *  The cut is deliberately conservative: anything whose meaning reaches across
 *  a blank line is left whole. Raw HTML (`<details>` around paragraphs), link
 *  reference definitions and footnotes are resolved against the whole document,
 *  so a reply using any of them is not cut at all. A list item after a blank
 *  line continues a list above it, and an indented line continues the item, so
 *  neither is a place to cut. Inside a fenced code block nothing is. Getting a
 *  cut wrong would show the reply differently while it streams than once it
 *  lands, and saving a parse is not worth that. */

const WHOLE_DOCUMENT = [
    /^ {0,3}<[A-Za-z!/?]/m, // raw HTML block
    /^ {0,3}\[[^\]]+\]:/m, // link reference definition
    /\[\^[^\]]+\]/, // footnote
];

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const LIST_ITEM = /^ {0,3}([-+*]|\d{1,9}[.)])(\s|$)/;
const BLANK = /^\s*$/;

export interface StreamBlocks {
    /** Finished blocks, each with the blank lines that end it. */
    settled: string[];
    /** Everything after the last finished block. */
    tail: string;
}

export function splitSettled(text: string): StreamBlocks {
    if (!text || WHOLE_DOCUMENT.some((pattern) => pattern.test(text))) return { settled: [], tail: text };

    const settled: string[] = [];
    let blockStart = 0;
    /* The block being read has a list in it, so a list item after a blank line
       belongs to it rather than starting something new. */
    let blockHasList = false;
    let fence: string | null = null;
    let afterBlank = false;

    let offset = 0;
    while (offset < text.length) {
        const end = text.indexOf("\n", offset);
        /* The last line may still be being typed, but how it starts is
           already known, and that is all a cut reads. Only a line that is
           nothing but spaces so far says nothing yet: it may be the indent of
           a continuation. */
        const partial = end < 0;
        const line = text.slice(offset, partial ? text.length : end);
        const lineStart = offset;
        offset = partial ? text.length : end + 1;
        if (partial && BLANK.test(line)) break;

        if (fence) {
            const closing = line.match(FENCE);
            if (closing && closing[1][0] === fence[0] && closing[1].length >= fence.length && BLANK.test(line.slice(line.indexOf(closing[1]) + closing[1].length))) {
                fence = null;
            }
            continue;
        }

        if (BLANK.test(line)) {
            afterBlank = true;
            continue;
        }

        const listItem = LIST_ITEM.test(line);
        if (afterBlank && lineStart > blockStart) {
            const continues = /^\s/.test(line) || (listItem && blockHasList);
            if (!continues) {
                settled.push(text.slice(blockStart, lineStart));
                blockStart = lineStart;
                blockHasList = false;
            }
        }
        afterBlank = false;
        if (listItem) blockHasList = true;

        const opening = line.match(FENCE);
        if (opening) fence = opening[1];
    }

    return { settled, tail: text.slice(blockStart) };
}
