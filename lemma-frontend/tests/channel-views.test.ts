import test from "node:test";
import assert from "node:assert/strict";
import { channelAbilities, mayStartChannel, readAbilities } from "../src/channels/abilities.ts";
import { readChannel } from "../src/channels/model.ts";
import { defaultStatus, emptyView, filterFor, isContactView, needsPerson, takesStatus } from "../src/channels/views.ts";

/** The Channels place's views -- the support inbox's among them -- and what
 *  each reader is offered in a channel. */

const contact = (row: Record<string, unknown>) => readChannel({ id: String(row.id), kind: "CONTACT", created_at: "", ...row })!;

test("each view is the API's own filter on the channel list", () => {
    assert.deepEqual(filterFor("all"), { archived: false });
    assert.deepEqual(filterFor("archived"), { archived: true });
    assert.deepEqual(filterFor("unassigned"), { kind: "CONTACT", archived: false, unassigned: true, status: "open" });
    assert.deepEqual(filterFor("mine"), { kind: "CONTACT", archived: false, assignee: "me" });
    assert.deepEqual(filterFor("needs"), { kind: "CONTACT", archived: false, needsPerson: true });
    assert.deepEqual(filterFor("needs", "closed"), { kind: "CONTACT", archived: false, needsPerson: true }, "needs a person is open by definition");
    assert.deepEqual(filterFor("contacts"), { kind: "CONTACT", archived: false });
});

test("a status picked narrows a contact view; any status is no status filter", () => {
    assert.deepEqual(filterFor("mine", "waiting_on_contact"), { kind: "CONTACT", archived: false, assignee: "me", status: "waiting_on_contact" });
    assert.deepEqual(filterFor("unassigned", null), { kind: "CONTACT", archived: false, unassigned: true });
    assert.equal(defaultStatus("unassigned"), "open");
    assert.equal(defaultStatus("mine"), null);
    assert.equal(isContactView("needs"), true);
    assert.equal(isContactView("archived"), false);
    assert.equal(takesStatus("mine"), true);
    assert.equal(takesStatus("needs"), false);
});

test("a row says it needs a person by the server's rule: open, nobody assigned, the agent off or asked for one", () => {
    const asked = contact({ id: "a", status: "open", attention: { since: "x", reason: "Refund" } });
    const off = contact({ id: "b", status: "open", agents_answer: false });
    const handled = contact({ id: "c", status: "open", attention: { since: "x" }, assignee: { kind: "MEMBER", id: "u", name: "Priya" } });
    const fine = contact({ id: "d", status: "open" });
    const closed = contact({ id: "e", status: "closed", agents_answer: false });
    assert.deepEqual([asked, off, handled, fine, closed].map(needsPerson), [true, true, false, false, false]);
});

test("an empty view says what it is empty of", () => {
    assert.match(emptyView("mine"), /assigned to you/);
    assert.match(emptyView("needs"), /Nobody is waiting/);
});

test("actions imply the weaker ones: manage writes, write reads, messaging contacts reads them", () => {
    assert.deepEqual(readAbilities(["channel.manage", "contact.message"]), { known: true, read: true, write: true, manage: true, contactRead: true, contactMessage: true });
    assert.deepEqual(readAbilities(["channel.read"]), { known: true, read: true, write: false, manage: false, contactRead: false, contactMessage: false });
    assert.equal(readAbilities(undefined).known, false);
    assert.equal(readAbilities(undefined).write, true, "nothing is withheld before the server has said");
    assert.equal(mayStartChannel(readAbilities(["channel.read"])), false);
});

test("a viewer reads and is told why they cannot write; a user writes but not to contacts", () => {
    const group = { kind: "GROUP" as const, me: { canManage: false } };
    const toContact = { kind: "CONTACT" as const, me: { canManage: false } };
    const viewer = channelAbilities(readAbilities(["channel.read", "contact.read"]), group, "Acme");
    assert.equal(viewer.write, false);
    assert.match(viewer.writeRefused ?? "", /You can read this chat/);
    const user = readAbilities(["channel.write"]);
    assert.equal(channelAbilities(user, group, "Acme").write, true);
    const userToContact = channelAbilities(user, toContact, "Acme");
    assert.equal(userToContact.write, false);
    assert.equal(userToContact.contactOps, false);
    assert.match(userToContact.writeRefused ?? "", /editors and admins/);
    const editor = channelAbilities(readAbilities(["channel.manage", "contact.message"]), toContact, "Acme");
    assert.deepEqual([editor.write, editor.contactOps, editor.manage], [true, true, false]);
});

test("a group's settings follow the channel's own can_manage, which answers for its owner too", () => {
    const user = readAbilities(["channel.write"]);
    assert.equal(channelAbilities(user, { kind: "GROUP", me: { canManage: true } }, "Acme").manage, true);
    assert.equal(channelAbilities(readAbilities(["channel.manage"]), { kind: "GROUP", me: { canManage: false } }, "Acme").manage, false);
    assert.equal(channelAbilities(user, { kind: "DIRECT", me: { canManage: true } }, "Acme").manage, false, "your own channel has no group settings");
});
