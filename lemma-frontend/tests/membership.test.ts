import test from "node:test";
import assert from "node:assert/strict";
import {
    alreadyKnown, canManage, canSetRole, inviteProblem, isLastOwner,
    memberEmail, memberName, roleLabel, unsentInvitation, type Member,
} from "../src/org/membership.ts";

function member(over: Partial<Member> = {}): Member {
    return { id: "m1", role: "ORG_MEMBER", user_id: "u1", user: { email: "sam@example.com" }, ...over };
}

test("a role reads as a word, and an unknown one still reads as something", () => {
    assert.equal(roleLabel("ORG_OWNER"), "Owner");
    assert.equal(roleLabel("ORG_EDITOR"), "Editor");
    assert.equal(roleLabel("ORG_MEMBER"), "Member");
    // a role this build has not heard of must not render as an empty cell
    assert.equal(roleLabel("ORG_BILLING"), "billing");
    assert.equal(roleLabel(null), "—");
});

test("only owners and editors may change the membership", () => {
    assert.equal(canManage("ORG_OWNER"), true);
    assert.equal(canManage("ORG_EDITOR"), true);
    assert.equal(canManage("ORG_MEMBER"), false);
    assert.equal(canManage(null), false);
});

test("only an owner may hand out ownership", () => {
    // Otherwise an editor promotes themselves by promoting a friend, or just
    // by promoting themselves — the same hole either way.
    assert.equal(canSetRole("ORG_EDITOR", "ORG_MEMBER"), true);
    assert.equal(canSetRole("ORG_EDITOR", "ORG_EDITOR"), true);
    assert.equal(canSetRole("ORG_EDITOR", "ORG_OWNER"), false);
    assert.equal(canSetRole("ORG_OWNER", "ORG_OWNER"), true);
    assert.equal(canSetRole("ORG_MEMBER", "ORG_MEMBER"), false);
});

test("the last owner cannot be removed, because nobody could change anything after", () => {
    const owner = member({ id: "a", role: "ORG_OWNER" });
    const other = member({ id: "b", role: "ORG_MEMBER" });

    assert.equal(isLastOwner([owner, other], "a"), true);
    assert.equal(isLastOwner([owner, other], "b"), false);
    // with a second owner there is no last one
    const second = member({ id: "c", role: "ORG_OWNER" });
    assert.equal(isLastOwner([owner, second, other], "a"), false);
    assert.equal(isLastOwner([], "a"), false);
});

test("somebody is called by their name, or their address, but never nothing", () => {
    assert.equal(memberName(member({ user: { first_name: "Sam", last_name: "Roy", email: "s@x.com" } })), "Sam Roy");
    assert.equal(memberName(member({ user: { email: "sam@example.com" } })), "sam@example.com");
    assert.equal(memberName(member({ user: null, email: null })), "Member");
    // the address is not repeated under itself when it is doing duty as the name
    assert.equal(memberEmail(member({ user: { email: "sam@example.com" } })), "");
    assert.equal(memberEmail(member({ user: { first_name: "Sam", email: "sam@example.com" } })), "sam@example.com");
});

test("an address is only refused when it cannot work", () => {
    // The server owns the rule. A regex that thinks it knows better is how a
    // real address gets refused by a form that never asked anyone.
    assert.equal(inviteProblem("sam@example.com"), null);
    assert.equal(inviteProblem("sam+tag@sub.example.co.uk"), null);
    assert.equal(inviteProblem("  sam@example.com "), null);
    assert.ok(inviteProblem(""));
    assert.ok(inviteProblem("sam at example.com"));
    assert.ok(inviteProblem("sam@@example.com"));
    assert.ok(inviteProblem("sam@"));
    assert.ok(inviteProblem("@example.com"));
});

test("nobody is asked twice", () => {
    const members = [member({ user: { email: "sam@example.com" } })];
    const pending = [{ email: "ravi@example.com", status: "PENDING" }, { email: "old@example.com", status: "REVOKED" }];

    assert.match(alreadyKnown("SAM@example.com", members, pending) ?? "", /already in/);
    assert.match(alreadyKnown("ravi@example.com", members, pending) ?? "", /already been invited/);
    // a revoked invitation is not an outstanding one, so they can be asked again
    assert.equal(alreadyKnown("old@example.com", members, pending), null);
    assert.equal(alreadyKnown("new@example.com", members, pending), null);
    assert.equal(alreadyKnown("", members, pending), null);
});

test("an invitation nobody was emailed says so, with the link to share instead", () => {
    const unsent = unsentInvitation({
        email: "sam@example.com",
        accept_url: "http://app.lemma.localhost/invitations/abc/accept",
        emailed: false,
    });
    assert.ok(unsent);
    assert.equal(unsent.link, "http://app.lemma.localhost/invitations/abc/accept");
    assert.match(unsent.said, /Email isn't set up on this server/);
    assert.match(unsent.said, /sam@example\.com/);
    assert.match(unsent.said, /Share this link/);
});

test("an emailed invitation, or one from a server that never said, needs no notice", () => {
    assert.equal(unsentInvitation({ email: "sam@example.com", emailed: true }), null);
    // An older server omits the field: not known to have failed, so not said to have.
    assert.equal(unsentInvitation({ email: "sam@example.com" }), null);
    assert.equal(unsentInvitation({ email: "sam@example.com", emailed: null }), null);
});
