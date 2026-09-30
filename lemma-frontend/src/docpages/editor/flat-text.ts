import type { Node as PMNode } from "@tiptap/pm/model";

/** A document as one string, with the position every character came from.
 *
 *  Comments are anchored to words, and words are found in a string; the
 *  editor speaks in ProseMirror positions. This is the one mapping between
 *  the two, so the text an anchor was taken from and the text it is searched
 *  in are always built the same way: text blocks joined by a newline, which
 *  is also how `textBetween(from, to, "\n")` reads a selection.
 */
export interface FlatText {
    text: string;
    /** `positions[i]` is the document position of `text[i]`. */
    positions: number[];
}

export function flatText(doc: PMNode): FlatText {
    let text = "";
    const positions: number[] = [];
    let first = true;
    doc.descendants((node, pos) => {
        if (node.isTextblock) {
            if (!first) {
                text += "\n";
                positions.push(pos);
            }
            first = false;
            node.forEach((child, offset) => {
                if (!child.isText || !child.text) return;
                for (let i = 0; i < child.text.length; i++) {
                    text += child.text[i];
                    positions.push(pos + 1 + offset + i);
                }
            });
            return false;
        }
        return true;
    });
    return { text, positions };
}

/** The string index of a document position, for turning a selection into an
 *  anchor. The first character at or after the position. */
export function indexAt(flat: FlatText, pos: number): number {
    const at = flat.positions.findIndex((one) => one >= pos);
    return at === -1 ? flat.text.length : at;
}

/** The document range of a string range. */
export function rangeOf(flat: FlatText, from: number, to: number): { from: number; to: number } | null {
    if (from < 0 || to > flat.text.length || from >= to) return null;
    return { from: flat.positions[from], to: flat.positions[to - 1] + 1 };
}
