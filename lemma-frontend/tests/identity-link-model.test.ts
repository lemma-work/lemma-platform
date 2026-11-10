import test from "node:test";
import assert from "node:assert/strict";
import { accountSaid, linkEnded, linkStep, readIdentityLinkStatus } from "../src/channels/identity-link-model.ts";
import { GIVE_REFUSED, grantSaid, mayGive, readTableGrant, toggled } from "../src/channels/table-grants-model.ts";

test("a code opened with no account is as good as gone", () => {
    assert.deepEqual(readIdentityLinkStatus({ state: "OPENED", account: { name: "Dana", username: null } }), { state: "OPENED", account: { name: "Dana", username: null } });
    assert.deepEqual(readIdentityLinkStatus({ state: "OPENED", account: null }), { state: "GONE" });
    assert.deepEqual(readIdentityLinkStatus({ state: "WAITING" }), { state: "WAITING" });
    assert.deepEqual(readIdentityLinkStatus({ state: "OTHER_ACCOUNT" }), { state: "OTHER_ACCOUNT" });
});

test("the proof's step follows what the server says, and a confirmed link ends it", () => {
    const opened = { state: "OPENED" as const, account: { name: "Dana", username: "dana" } };
    assert.equal(linkStep(undefined, false), "open");
    assert.equal(linkStep({ state: "WAITING" }, false), "open");
    assert.equal(linkStep(opened, false), "confirm");
    assert.equal(linkStep({ state: "OTHER_ACCOUNT" }, false), "other-account");
    assert.equal(linkStep({ state: "GONE" }, false), "gone");
    assert.equal(linkStep(opened, true), "linked");
    assert.deepEqual((["open", "confirm", "linked", "other-account", "gone"] as const).filter(linkEnded), ["linked", "other-account", "gone"]);
});

test("an account is named by its name and handle, either, or neither", () => {
    assert.equal(accountSaid({ name: " Dana ", username: "dana" }), "Dana (@dana)");
    assert.equal(accountSaid({ name: null, username: "dana" }), "@dana");
    assert.equal(accountSaid({ name: "Dana", username: null }), "Dana");
    assert.equal(accountSaid({ name: "  ", username: null }), "this account");
});

test("a table grant says what it lets people read and add, and needs a column to be given", () => {
    const grant = readTableGrant({ table: "orders", read_columns: ["item", "status"], add_columns: ["item"] });
    assert.deepEqual(grant, { table: "orders", readColumns: ["item", "status"], addColumns: ["item"] });
    assert.equal(grantSaid(grant), "read item, status · add a row with item");
    assert.equal(grantSaid({ readColumns: [], addColumns: ["note"] }), "add a row with note");
    assert.deepEqual([...toggled(new Set(["a"]), "b")], ["a", "b"]);
    assert.deepEqual([...toggled(new Set(["a", "b"]), "a")], ["b"]);
    assert.equal(mayGive("", new Set(["a"]), new Set()), false);
    assert.equal(mayGive("orders", new Set(), new Set()), false);
    assert.equal(mayGive("orders", new Set(), new Set(["a"])), true);
    assert.match(GIVE_REFUSED, /owners or an admin/);
});
