import assert from "node:assert/strict";
import { test } from "node:test";
import { isAnalyticsDocument } from "../src/site/analytics/document.ts";

test("only the top-level document owns analytics and consent", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "window");
    try {
        Reflect.deleteProperty(globalThis, "window");
        assert.equal(isAnalyticsDocument(), false);
        const parent = {};
        Object.defineProperty(globalThis, "window", { configurable: true, value: { self: parent, top: parent } });
        assert.equal(isAnalyticsDocument(), true);
        Object.defineProperty(globalThis, "window", { configurable: true, value: { self: {}, top: parent } });
        assert.equal(isAnalyticsDocument(), false);
    } finally {
        if (original) Object.defineProperty(globalThis, "window", original);
        else Reflect.deleteProperty(globalThis, "window");
    }
});
