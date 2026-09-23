import test from "node:test";
import assert from "node:assert/strict";
import {
    agentCandidates,
    itemsOf,
    nameOf,
    personCandidates,
    recordSourcesFrom,
    scheduleCandidates,
    tableCandidates,
    likePattern,
    recordSearchSql,
    sqlLiteral,
    MAX_RECORD_TABLES,
} from "../src/search/sources.ts";

test("a list is a list whichever shape it arrived in", () => {
    assert.deepEqual(itemsOf([{ id: "a" }]), [{ id: "a" }]);
    assert.deepEqual(itemsOf({ items: [{ id: "a" }] }), [{ id: "a" }]);
    assert.deepEqual(itemsOf(null), []);
    assert.deepEqual(itemsOf(undefined), []);
    assert.deepEqual(itemsOf({ items: "not a list" }), []);
    assert.deepEqual(itemsOf("nope"), []);
});

test("a name is whichever of the several the API used", () => {
    // These lists genuinely disagree about which field holds the name, and a
    // reader that knows only one produces a column of blank rows.
    assert.equal(nameOf({ name: "Atlas" }), "Atlas");
    assert.equal(nameOf({ title: "Atlas" }), "Atlas");
    assert.equal(nameOf({ display_name: "Atlas" }), "Atlas");
    assert.equal(nameOf({ email: "a@b.c" }), "a@b.c");
    assert.equal(nameOf({ name: "   " , title: "Atlas" }), "Atlas", "blank is not a name");
    assert.equal(nameOf({}), "Untitled");
    assert.equal(nameOf({}, "Agent"), "Agent");
});

test("a person is read from the shape the API actually returns", () => {
    // Observed on a live pod. None of the field names are the obvious ones:
    // `user_name` not `user.first_name`, `roles` not `role`, `pod_member_id`
    // not `id`. Guessing the nested shape rendered every real person as the
    // word "Member" — unfindable by name, in a box claiming to cover people.
    const [person] = personCandidates({ items: [{
        pod_member_id: "01a0-member",
        user_id: "9452-user",
        email: "deepak@example.com",
        user_email: "deepak@example.com",
        user_name: "Deepak Jh",
        roles: ["POD_ADMIN"],
    }] });

    assert.equal(person.title, "Deepak Jh");
    assert.equal(person.subtitle, "deepak@example.com");
    assert.equal(person.id, "01a0-member");
    assert.match(person.haystack ?? "", /admin/);
});

test("a person with no name is found by their email, and says what they may do", () => {
    const [person] = personCandidates({ items: [{ pod_member_id: "m", user_email: "sam@example.com", roles: ["POD_VIEWER"] }] });

    assert.equal(person.title, "sam@example.com");
    assert.equal(person.subtitle, "viewer");
});

test("a member row with nothing readable is still a row, not a crash", () => {
    const [person] = personCandidates({ items: [{ pod_member_id: "m3" }] });

    assert.equal(person.title, "Member");
    assert.equal(person.kind, "person");
    assert.equal(person.id, "m3");
});

test("a schedule can be found by when it runs, not only what it is called", () => {
    // "every weekday" is a thing somebody types, and it is not in the name.
    const [schedule] = scheduleCandidates({ items: [{ id: "s1", name: "Morning sweep", cron: "0 9 * * 1-5", agent_name: "atlas" }] });

    assert.equal(schedule.title, "Morning sweep");
    assert.match(schedule.haystack ?? "", /0 9 \* \* 1-5/);
    assert.match(schedule.haystack ?? "", /atlas/);
});

test("a malformed row does not take the list down with it", () => {
    const candidates = agentCandidates({ items: [{}, { name: 42 }, { name: "Real" }] });

    assert.equal(candidates.length, 3);
    assert.deepEqual(candidates.map((c) => c.title), ["Agent", "Agent", "Real"]);
    assert.ok(candidates.every((c) => typeof c.id === "string" && c.id.length > 0), "every row needs a key");
});

test("a table with nothing readable in it is not queried at all", () => {
    // There is nothing in an INTEGER or a UUID that a typed phrase could match,
    // and asking spends a round trip to be told so. A VECTOR is an embedding.
    const { sources } = recordSourcesFrom([
        { name: "metrics", columns: [{ name: "id", type: "UUID" }, { name: "count", type: "INTEGER" }] },
        { name: "embeddings", columns: [{ name: "vec", type: "VECTOR" }] },
        { name: "notes", columns: [{ name: "id", type: "UUID" }, { name: "body", type: "TEXT" }] },
    ]);

    assert.deepEqual(sources.map((s) => s.tableName), ["notes"]);
    assert.deepEqual(sources[0].searchFields, ["body"]);
});

test("every searchable text type counts", () => {
    const { sources } = recordSourcesFrom([
        { name: "docs", columns: [{ name: "body", type: "TEXT" }, { name: "stage", type: "ENUM" }, { name: "at", type: "FILE_PATH" }, { name: "n", type: "FLOAT" }] },
    ]);

    assert.deepEqual(sources[0].searchFields, ["body", "stage", "at"]);
    assert.equal(sources[0].displayField, "body", "the first text column is what a row gets called");
});

test("record search is bounded, and says how much it left out", () => {
    // Records cost a request per table, so an unbounded pod turns one keystroke
    // into thirty round trips. The bound is reported rather than silent.
    const many = Array.from({ length: MAX_RECORD_TABLES + 5 }, (_, i) => ({
        name: "t" + i,
        columns: [{ name: "body", type: "TEXT" }],
    }));

    const { sources, skipped } = recordSourcesFrom(many);

    assert.equal(sources.length, MAX_RECORD_TABLES);
    assert.equal(skipped, 5);
});

test("a table with no columns loaded yet is skipped rather than guessed at", () => {
    assert.deepEqual(recordSourcesFrom([{ name: "unknown" }]).sources, []);
    assert.deepEqual(recordSourcesFrom([]).sources, []);
});

test("a table list still produces findable tables", () => {
    const [table] = tableCandidates({ items: [{ id: "t1", name: "invoices", column_count: 7 }] });

    assert.equal(table.title, "invoices");
    assert.equal(table.subtitle, "7 columns");
});

test("a quote in the query cannot end the literal it sits in", () => {
    // The datastore endpoint takes SQL with no parameter binding, so this is
    // the only place the escaping can happen.
    assert.equal(sqlLiteral("o'brien"), "'o''brien'");
    assert.equal(sqlLiteral("'; DROP TABLE users; --"), "'''; DROP TABLE users; --'");
    assert.equal(sqlLiteral("'''"), "''''''''");
    assert.equal(sqlLiteral("plain"), "'plain'");
});

test("a backslash is an ordinary character in a standard-conforming literal", () => {
    // Doubling the quote is the whole escape under standard_conforming_strings,
    // on by default since PostgreSQL 9.1. Treating a backslash as an escape
    // would be the bug, not the fix.
    assert.equal(sqlLiteral("back\\slash"), "'back\\slash'");
    assert.equal(sqlLiteral("\\'"), "'\\'''");
});

test("a NUL is dropped and length is capped", () => {
    // Postgres text cannot hold a NUL, and a search box is not a delivery
    // mechanism for a megabyte.
    assert.equal(sqlLiteral("a\0b"), "'ab'");
    assert.equal(sqlLiteral("x".repeat(500)).length, 202);
});

test("wildcards typed by a person are matched literally", () => {
    // `%` means "everything" to LIKE, so leaving it raw turns a search for a
    // percentage into a search for every row in the table.
    assert.equal(likePattern("50%"), "'%50\\%%'");
    assert.equal(likePattern("a_b"), "'%a\\_b%'");
    assert.equal(likePattern("back\\slash"), "'%back\\\\slash%'");
});

test("a column name is quoted as an identifier, and the query is not spliced into it", () => {
    const sql = recordSearchSql(
        { key: "k", tableName: "invoices", label: "invoices", searchFields: ["body", "sta\"tus"], limit: 5 },
        "o'brien",
    );

    assert.match(sql, /FROM "invoices"/);
    assert.match(sql, /"body"::text ILIKE '%o''brien%' ESCAPE/);
    assert.match(sql, /"sta""tus"::text/, "a quote in a column name is doubled too");
    assert.match(sql, / OR /, "every text column is searched, not just the first");
    assert.match(sql, /LIMIT 5$/);
});

test("a table named to break out of its own quoting cannot", () => {
    const sql = recordSearchSql(
        { key: "k", tableName: 'x" UNION SELECT * FROM secrets --', label: "x", searchFields: ["a"], limit: 1 },
        "q",
    );

    assert.match(sql, /FROM "x"" UNION SELECT \* FROM secrets --"/);
});
