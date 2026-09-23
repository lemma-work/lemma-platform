import assert from "node:assert/strict";
import { test } from "node:test";
import { originalRows, normalizeDate, validateRows } from "../src/marketing/apps/import-model.ts";
import { evidenceCounts, interviews } from "../src/marketing/apps/research-model.ts";

test("normalizing dates preserves the source and does not hide email errors", () => {
    const snapshot = JSON.stringify(originalRows);
    assert.equal(validateRows(originalRows).length, 4);
    const normalized = originalRows.map(row => ({ ...row, joined: normalizeDate(row.joined) }));
    assert.deepEqual(validateRows(normalized).map(issue => issue.field), ["email"]);
    const fixed = normalized.map(row => row.id === 3 ? { ...row, email: "team@wren.example" } : row);
    assert.equal(validateRows(fixed).length, 0);
    assert.equal(JSON.stringify(originalRows), snapshot);
    assert.equal(normalizeDate("31/02/2026"), "31/02/2026");
    assert.equal(validateRows([{ ...fixed[0], joined: "2026-02-31" }]).length, 1);
});

test("research counts track exclusions, recoding and empty selections", () => {
    const base = evidenceCounts(interviews);
    assert.equal(base.total, 5);
    assert.equal(base.themes[0].count, 3);
    const excluded = interviews.map(source => source.id === "N1" ? { ...source, included: false } : source);
    assert.equal(evidenceCounts(excluded).total, 4);
    assert.equal(evidenceCounts(excluded).themes[0].count, 2);
    assert.equal(evidenceCounts(interviews.map(source => ({ ...source, included: false }))).total, 0);
    assert.equal(evidenceCounts(interviews.map(source => source.id === "N1" ? { ...source, theme: "No owner" } : source)).themes[2].count, 2);
});
