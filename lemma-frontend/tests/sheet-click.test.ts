import test from "node:test";
import assert from "node:assert/strict";
import { nearestInBox } from "../src/docpages/editor/sheet-click.ts";

/* The text of a page, and the sheet's padding around it. */
const box = { left: 100, top: 200, right: 860, bottom: 700 };

test("a click under the last line still asks for the end", () => {
    assert.deepEqual(nearestInBox({ left: 300, top: 940 }, box), { left: 300, top: 700 });
});

test("a click in the top margin asks for the start, not the end", () => {
    assert.deepEqual(nearestInBox({ left: 300, top: 40 }, box), { left: 300, top: 200 });
});

test("a click beside the measure stays on its own line", () => {
    assert.deepEqual(nearestInBox({ left: 20, top: 420 }, box), { left: 100, top: 420 });
    assert.deepEqual(nearestInBox({ left: 1400, top: 420 }, box), { left: 860, top: 420 });
});

test("a click in the text is left where it is", () => {
    assert.deepEqual(nearestInBox({ left: 300, top: 420 }, box), { left: 300, top: 420 });
});

test("a corner click is brought in on both axes", () => {
    assert.deepEqual(nearestInBox({ left: 1400, top: 940 }, box), { left: 860, top: 700 });
});

test("a box with no area has no nearest point", () => {
    assert.equal(nearestInBox({ left: 300, top: 420 }, { left: 100, top: 200, right: 100, bottom: 700 }), null);
    assert.equal(nearestInBox({ left: 300, top: 420 }, { left: 100, top: 200, right: 860, bottom: 200 }), null);
});
