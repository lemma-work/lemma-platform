import test from "node:test";
import assert from "node:assert/strict";
import { applySpec, blankSpec, buildSql, readView, writeView } from "../src/docpages/views/spec.ts";

test("a spec becomes quoted, escaped SQL", () => {
    const spec = { ...blankSpec("leads"), columns: ["name", "order"], filters: [{ column: "status", op: "=" as const, value: "O'Brien" }, { column: "amount", op: ">" as const, value: "500" }], sort: { column: "created_at", dir: "desc" as const }, limit: 20 };
    assert.equal(buildSql(spec), `SELECT "name", "order" FROM "leads" WHERE "status" = 'O''Brien' AND "amount" > '500' ORDER BY "created_at" DESC LIMIT 20`);
    assert.equal(buildSql({ ...blankSpec("leads"), filters: [{ column: "note", op: "contains", value: "50%" }] }), `SELECT * FROM "leads" WHERE "note"::text ILIKE '%50\\%%' LIMIT 50`);
    assert.throws(() => buildSql(blankSpec("leads; drop table x")));
});

test("grouping summarises each group, biggest first", () => {
    assert.equal(buildSql({ ...blankSpec("deals"), group: { column: "stage", aggregate: "count", of: null } }), `SELECT "stage", COUNT(*) AS count FROM "deals" GROUP BY "stage" ORDER BY count DESC LIMIT 50`);
    assert.equal(buildSql({ ...blankSpec("deals"), group: { column: "stage", aggregate: "sum", of: "value" } }), `SELECT "stage", SUM("value") AS "sum_value" FROM "deals" GROUP BY "stage" ORDER BY "sum_value" DESC LIMIT 50`);
});

test("the block round-trips its title, spec and SQL; hand-written SQL has no spec", () => {
    const spec = { ...blankSpec("leads"), filters: [{ column: "status", op: "=" as const, value: "New" }] };
    const block = { title: "New leads", spec, sql: buildSql(spec) };
    assert.deepEqual(readView(writeView(block)), block);
    const hand = readView("-- Joined\nSELECT l.name, c.name FROM leads l JOIN companies c ON c.id = l.company_id");
    assert.equal(hand.title, "Joined");
    assert.equal(hand.spec, null);
    assert.match(hand.sql, /JOIN companies/);
});

test("in memory, a spec does what its SQL would", () => {
    const rows = [
        { name: "a", stage: "won", value: 10 },
        { name: "b", stage: "won", value: 5 },
        { name: "c", stage: "lost", value: 7 },
        { name: "d", stage: "", value: 1 },
    ];
    assert.deepEqual(applySpec(rows, { ...blankSpec("x"), filters: [{ column: "stage", op: "=", value: "won" }], sort: { column: "value", dir: "asc" }, columns: ["name"] }), [{ name: "b" }, { name: "a" }]);
    assert.deepEqual(applySpec(rows, { ...blankSpec("x"), group: { column: "stage", aggregate: "sum", of: "value" } }), [{ stage: "won", sum_value: 15 }, { stage: "lost", sum_value: 7 }, { stage: "", sum_value: 1 }]);
    assert.equal(applySpec(rows, { ...blankSpec("x"), filters: [{ column: "stage", op: "empty", value: "" }] }).length, 1);
});

test("a contains filter matches a backslash, a percent and an underscore literally", () => {
    const sql = buildSql({ ...blankSpec("notes"), filters: [{ column: "body", op: "contains" as const, value: "a\\_b%" }] });
    assert.equal(sql, `SELECT * FROM "notes" WHERE "body"::text ILIKE '%a\\\\\\_b\\%%' LIMIT 50`);
});
