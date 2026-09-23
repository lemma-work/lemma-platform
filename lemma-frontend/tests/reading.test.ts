import test from "node:test";
import assert from "node:assert/strict";
import { compact, dayStart, readableName, readableNumber, whenever } from "../src/library/reading.ts";

test("a bare date is a day on a calendar, not midnight in UTC", () => {
    // `Date.parse("2026-09-19")` is midnight UTC, which is the evening of the
    // 18th in half the world. A checklist compared it against local midnight
    // and listed today's work as "yesterday", in red.
    const day = new Date(dayStart("2026-09-19"));

    assert.equal(day.getFullYear(), 2026);
    assert.equal(day.getMonth(), 8);
    assert.equal(day.getDate(), 19);
    assert.equal(day.getHours(), 0, "local midnight, wherever the reader is");
});

test("a value with a time in it really is a moment", () => {
    assert.equal(dayStart("2026-09-19T14:30:00Z"), Date.parse("2026-09-19T14:30:00Z"));
});

test("a date reads as the reader would say it", () => {
    const now = new Date(2026, 8, 19, 11, 0, 0);

    assert.equal(whenever("2026-09-19", now)?.text, "today");
    assert.equal(whenever("2026-09-20", now)?.text, "tomorrow");
    assert.equal(whenever("2026-09-18", now)?.text, "yesterday");
    assert.equal(whenever("2026-09-24", now)?.text, "in 5 days");
    assert.equal(whenever("2026-09-13", now)?.text, "6 days ago");
});

test("today is today whatever time of day it is asked", () => {
    // Late in the evening is where an hours-based difference rounds over into
    // the next day and a task due today starts saying "tomorrow".
    for (const hour of [0, 6, 12, 18, 23]) {
        const now = new Date(2026, 8, 19, hour, 45, 0);
        assert.equal(whenever("2026-09-19", now)?.text, "today", "at " + hour + ":45");
    }
});

test("overdue is negative, so a list can colour it", () => {
    const now = new Date(2026, 8, 19, 11, 0, 0);

    assert.ok((whenever("2026-09-17", now)?.days ?? 0) < 0);
    assert.ok((whenever("2026-09-21", now)?.days ?? 0) > 0);
});

test("a value that is not a date does not become one", () => {
    assert.equal(whenever("not a date"), null);
    assert.equal(whenever(null), null);
    assert.equal(whenever(""), null);
});

test("numbers shorten without losing what they were", () => {
    assert.equal(compact(0), "0");
    assert.equal(compact(942), "942");
    assert.equal(compact(1240), "1.2k");
    assert.equal(compact(18400), "18k");
    assert.equal(compact(2_100_000), "2.1m");
    assert.equal(compact(-1500), "-1.5k");
});

test("a long number gets separators and a year does not", () => {
    assert.equal(readableNumber(17655), "17,655");
    assert.equal(readableNumber(1_250_000), "1,250,000");
    // Left alone below ten thousand, which is where a year lives.
    assert.equal(readableNumber(2026), null);
    assert.equal(readableNumber(4000), null);
    assert.equal(readableNumber("not a number"), null);
});

test("a table name reads as a person would write it", () => {
    assert.equal(readableName("content_ideas"), "Content Ideas");
    assert.equal(readableName("metrics_daily"), "Metrics Daily");
    assert.equal(readableName("outreach-queue"), "Outreach Queue");
    assert.equal(readableName("contacts"), "Contacts");
});

test("an acronym is shouted, not sentence-cased", () => {
    // "Gtm Targets" and "Api Keys" read as typos.
    assert.equal(readableName("gtm_targets"), "GTM Targets");
    assert.equal(readableName("api_keys"), "API Keys");
    assert.equal(readableName("sla_breaches"), "SLA Breaches");
    // No vowel and short is an acronym without needing to be listed.
    assert.equal(readableName("crm_sync"), "CRM Sync");
    assert.equal(readableName("sdk"), "SDK");
});

test("a name that was already written for people is left as it was", () => {
    // Only the first letter is touched, so a capital inside a word survives.
    assert.equal(readableName("LEDFlex"), "LEDFlex");
    assert.equal(readableName("Customer Pulse"), "Customer Pulse");
});

test("nothing sensible in, the same thing out", () => {
    assert.equal(readableName(""), "");
    assert.equal(readableName("___"), "___");
});
