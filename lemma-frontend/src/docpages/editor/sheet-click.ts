/** A point in viewport coordinates — a click, or the box the text occupies. */
export type Point = { left: number; top: number };
export type Box = { left: number; top: number; right: number; bottom: number };

/** Where a click on the sheet, outside the text, asks for the caret. */
export type CaretTarget = Point | "start" | "end";

/** The caret a click on the sheet's margin means.
 *
 *  A page's sheet is wider and taller than the text in it: the padding above
 *  the first line, beside the measure and under the last one all belong to the
 *  page, and a click in any of them has to land where it was aimed. ProseMirror
 *  resolves a caret only for a point inside its own box, so the click is
 *  brought to the nearest point that is — and that is the whole difference
 *  between a click that follows the aim and one that throws the caret to the
 *  end.
 *
 *  The two horizontal margins are a point on the nearest line, so the click's x
 *  is clamped and its y is left alone. The two vertical ones are not points at
 *  all: a click past the last line means the end of the page and one above the
 *  first line means the start, wherever across the measure it fell. Clamping
 *  those to the edge of the box instead resolves them to the character nearest
 *  the click's x, which is mid-line on the last line rather than after it.
 *
 *  `null` for a box with no area: an editor that has not laid out yet has no
 *  nearest point to offer. */
export function caretTarget(at: Point, box: Box): CaretTarget | null {
    if (box.right <= box.left || box.bottom <= box.top) return null;
    if (at.top >= box.bottom) return "end";
    if (at.top < box.top) return "start";
    return { left: Math.min(Math.max(at.left, box.left), box.right), top: at.top };
}
