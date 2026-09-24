import test from "node:test";
import assert from "node:assert/strict";
import { handleBrowserKeyDown } from "../src/computer/keyboard.ts";
import { startupProgress } from "../src/computer/startup.ts";

test("native paste reaches the browser without noVNC cancelling it", () => {
    for (const modifier of ["metaKey", "ctrlKey"] as const) {
        let stopped = false;
        let prevented = false;
        const sent: number[] = [];
        handleBrowserKeyDown({ key: "v", metaKey: false, ctrlKey: false, altKey: false,
            [modifier]: true, stopPropagation: () => { stopped = true; },
            preventDefault: () => { prevented = true; },
        }, { viewOnly: false, sendKey: (key) => { sent.push(key); } });
        assert.equal(stopped, true);
        assert.equal(prevented, false);
        assert.deepEqual(sent, []);
    }
});

test("view-only panes leave keyboard events untouched", () => {
    handleBrowserKeyDown({ key: "v", metaKey: true, ctrlKey: false, altKey: false,
        stopPropagation: () => assert.fail("intercepted view-only key"),
        preventDefault: () => assert.fail("cancelled view-only key"),
    }, { viewOnly: true, sendKey: () => assert.fail("sent a view-only key") });
});

test("startup reports measured download progress only with a valid total", () => {
    assert.deepEqual(startupProgress({ state: "downloading", done_mb: 25, total_mb: 100 }), {
        title: "Downloading your workspace", detail: null, fraction: 0.25, downloaded: "25 of 100 MB",
    });
    for (const total_mb of [undefined, 0, -1, NaN]) {
        assert.equal(startupProgress({ state: "downloading", done_mb: 25, total_mb })?.fraction, null);
    }
    assert.equal(startupProgress({ state: "starting", done_mb: 25, total_mb: 100 })?.fraction, null);
    assert.equal(startupProgress({ state: "ready" }), null);
    assert.equal(startupProgress({ state: "asleep" }), null);
});
