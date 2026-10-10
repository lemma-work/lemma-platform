import test from "node:test";
import assert from "node:assert/strict";
import { caretTarget } from "../src/docpages/editor/sheet-click.ts";

/* The text of a page, and the sheet's padding around it. */
const box = { left: 100, top: 200, right: 860, bottom: 700 };

test("a click past the last line asks for the end, wherever across the measure", () => {
    assert.equal(caretTarget({ left: 300, top: 940 }, box), "end");
    assert.equal(caretTarget({ left: 1400, top: 940 }, box), "end");
    assert.equal(caretTarget({ left: 20, top: 940 }, box), "end");
});

test("a click above the first line asks for the start", () => {
    assert.equal(caretTarget({ left: 300, top: 40 }, box), "start");
    assert.equal(caretTarget({ left: 1400, top: 40 }, box), "start");
});

test("a click beside the measure stays on its own line", () => {
    assert.deepEqual(caretTarget({ left: 20, top: 420 }, box), { left: 100, top: 420 });
    assert.deepEqual(caretTarget({ left: 1400, top: 420 }, box), { left: 860, top: 420 });
});

test("a click in the text is left where it is", () => {
    assert.deepEqual(caretTarget({ left: 300, top: 420 }, box), { left: 300, top: 420 });
});

test("the box's own edges are inside it, on the line they touch", () => {
    assert.deepEqual(caretTarget({ left: 300, top: 200 }, box), { left: 300, top: 200 });
    assert.deepEqual(caretTarget({ left: 300, top: 699 }, box), { left: 300, top: 699 });
});

test("a box with no area has no target", () => {
    assert.equal(caretTarget({ left: 300, top: 420 }, { left: 100, top: 200, right: 100, bottom: 700 }), null);
    assert.equal(caretTarget({ left: 300, top: 420 }, { left: 100, top: 200, right: 860, bottom: 200 }), null);
});
