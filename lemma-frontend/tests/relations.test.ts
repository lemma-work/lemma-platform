import test from "node:test";
import assert from "node:assert/strict";
import { inwardLinks, linkLabel, outwardLinks, parseReference, type TableShape } from "../src/library/relations.ts";

const issues: TableShape = {
    name: "issues",
    primary_key_column: "id",
    columns: [
        { name: "id", type: "UUID" },
        { name: "title", type: "TEXT" },
        { name: "owner_id", type: "UUID", foreign_key: { references: "users.id" } },
        { name: "approver_id", type: "UUID", foreign_key: { references: "users.id" } },
    ],
};
const comments: TableShape = {
    name: "comments",
    columns: [
        { name: "id", type: "UUID" },
        { name: "issue_id", type: "UUID", foreign_key: { references: "issues.id" } },
    ],
};
const users: TableShape = { name: "users", primary_key_column: "id", columns: [{ name: "id", type: "UUID" }] };

test("a reference is parsed, not split blindly", () => {
    // Following a malformed one means querying a table called "".
    assert.deepEqual(parseReference("users.id"), { table: "users", column: "id" });
    assert.equal(parseReference("users"), null);
    assert.equal(parseReference(".id"), null);
    assert.equal(parseReference("users."), null);
    assert.equal(parseReference(""), null);
    assert.equal(parseReference(null), null);
    assert.equal(parseReference(undefined), null);
});

test("where this row points is read off its own columns", () => {
    assert.deepEqual(outwardLinks(issues), [
        { column: "owner_id", table: "users", referencedColumn: "id" },
        { column: "approver_id", table: "users", referencedColumn: "id" },
    ]);
});

test("two columns pointing at one table are two links", () => {
    // `owner_id` and `approver_id` mean different things. Collapsing them by
    // table would show one relation and lose which is which.
    assert.equal(outwardLinks(issues).filter((l) => l.table === "users").length, 2);
});

test("a table with nothing declared points nowhere", () => {
    assert.deepEqual(outwardLinks(users), []);
    assert.deepEqual(outwardLinks(null), []);
    assert.deepEqual(outwardLinks({ name: "x" }), []);
});

test("what points back here is read off everybody else's columns", () => {
    // The reference is declared on the table doing the pointing, never on the
    // one pointed at.
    assert.deepEqual(inwardLinks([issues, comments, users], "issues"), [
        { table: "comments", column: "issue_id", referencedColumn: "id" },
    ]);
});

test("a table does not reference itself into its own inward list", () => {
    const tree: TableShape = {
        name: "tree",
        columns: [{ name: "parent_id", foreign_key: { references: "tree.id" } }],
    };

    assert.deepEqual(inwardLinks([tree], "tree"), []);
});

test("inward links are found whatever case the schema used", () => {
    assert.equal(inwardLinks([comments], "ISSUES").length, 1);
});

test("a linked row is named by something readable, not by its key", () => {
    // A foreign key is a uuid, and "01a0b12c…" as the whole of a link tells
    // nobody what is on the other end.
    assert.equal(linkLabel({ id: "01a0", name: "Acme Ltd" }, "01a0"), "Acme Ltd");
    assert.equal(linkLabel({ id: "01a0", email: "sam@example.com" }, "01a0"), "sam@example.com");
    assert.equal(linkLabel({ id: "01a0" }, "01a0"), "01a0");
    assert.equal(linkLabel(null, "01a0"), "01a0");
    assert.equal(linkLabel({ id: "01a0", name: "   " }, "01a0"), "01a0", "blank is not a name");
});
