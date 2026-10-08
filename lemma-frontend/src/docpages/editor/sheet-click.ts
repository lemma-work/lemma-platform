/** A point in viewport coordinates — a click, or the box the text occupies. */
export type Point = { left: number; top: number };
export type Box = { left: number; top: number; right: number; bottom: number };

/** The nearest point inside `box` to `at`.
 *
 *  A page's sheet is wider and taller than the text in it: the padding above
 *  the first line, beside the measure and under the last one all belong to the
 *  page, and a click in any of them has to land where it was aimed.
 *  ProseMirror resolves a caret only for a point inside its own box, so the
 *  click is brought to the nearest point that is — and that is the whole
 *  difference between a click that follows the aim and one that throws the
 *  caret to the end. The space below the last line still resolves to the end,
 *  the top margin to the start, and a side margin to the nearest line.
 *
 *  `null` for a box with no area: an editor that has not laid out yet has no
 *  nearest point to offer. */
export function nearestInBox(at: Point, box: Box): Point | null {
    if (box.right <= box.left || box.bottom <= box.top) return null;
    return {
        left: Math.min(Math.max(at.left, box.left), box.right),
        top: Math.min(Math.max(at.top, box.top), box.bottom),
    };
}
