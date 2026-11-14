import test from "node:test";
import assert from "node:assert/strict";
import { currentZoneName } from "../src/data/time-zone.ts";

test("a zone the tz database renamed is sent by its current name", () => {
    assert.equal(currentZoneName("Asia/Calcutta"), "Asia/Kolkata", "what Chrome reports for India");
    assert.equal(currentZoneName("Europe/Kiev"), "Europe/Kyiv");
    assert.equal(currentZoneName("Asia/Saigon"), "Asia/Ho_Chi_Minh");
});

test("any other zone is sent as the browser gave it", () => {
    assert.equal(currentZoneName("Europe/Berlin"), "Europe/Berlin");
    assert.equal(currentZoneName("UTC"), "UTC");
    assert.equal(currentZoneName("constructor"), "constructor", "a word every object answers to is not a zone that was renamed");
});
