import test from "node:test";
import assert from "node:assert/strict";
import {
    groupLine,
    groupsSupported,
    groupTitle,
    linkShown,
    noGroupsYet,
    offersTakeOver,
    readSurfaceGroup,
    readSurfaceGroups,
    whoMayChange,
    withGroup,
} from "../src/data/surface-groups.ts";
import type { Member, SurfaceGroup } from "../src/data/types.ts";

/** A channel's groups: what a row says, and what it offers. */

function group(partial: Partial<SurfaceGroup> = {}): SurfaceGroup {
    return {
        id: "7f3c9a2e-4b1d-4e8a-9c55-1d2e3f4a5b6c",
        platform: "TELEGRAM",
        title: "Design partners",
        externalId: "-1001839204417",
        inviteLink: null,
        pending: false,
        owner: { userId: "priya-user", name: "Priya" },
        answersOutsiders: true,
        welcomesOutsiders: true,
        botAnswersOutsiders: true,
        canManage: true,
        updatedAt: "2026-09-30T10:00:00Z",
        ...partial,
    };
}

test("reads a group off the wire, and refuses one without an id", () => {
    const read = readSurfaceGroup({
        id: "g1", platform: "telegram", external_channel_id: "-100", title: "  Launch crew ",
        invite_link: null, pending: false,
        owner: { user_id: "u1", display_name: "" }, answers_outsiders: true, welcomes_outsiders: false,
        updated_at: "2026-09-30T10:00:00Z",
    });
    assert.deepEqual(read, {
        id: "g1", platform: "TELEGRAM", title: "Launch crew", externalId: "-100", inviteLink: null, pending: false,
        owner: { userId: "u1", name: null }, answersOutsiders: true, welcomesOutsiders: false,
        // As the API defaults them: the bot's switch on, nothing the reader's to change.
        botAnswersOutsiders: true, canManage: false,
        updatedAt: "2026-09-30T10:00:00Z",
    });
    assert.equal(readSurfaceGroup({ title: "no id" }), null);
    assert.equal(readSurfaceGroup({ id: "g2", owner: { display_name: "Nobody's id" } })?.owner, null);
    assert.deepEqual(readSurfaceGroups({ items: [{ id: "g1" }, null, { id: "" }] }).map((one) => one.id), ["g1"]);
    assert.deepEqual(readSurfaceGroups(null), []);
});

test("a WhatsApp group the bot opened carries its link; one still being made has neither id nor link", () => {
    const opened = readSurfaceGroup({
        id: "w1", platform: "WHATSAPP", external_channel_id: "120363041977712345@g.us",
        invite_link: "https://chat.whatsapp.com/Kx4vQ9", pending: false,
    });
    assert.equal(opened?.inviteLink, "https://chat.whatsapp.com/Kx4vQ9");
    assert.equal(opened?.pending, false);
    const making = readSurfaceGroup({ id: "w2", platform: "WHATSAPP", external_channel_id: null, invite_link: "https://chat.whatsapp.com/early", pending: true });
    assert.equal(making?.externalId, null);
    assert.equal(making?.pending, true);
    assert.equal(making?.inviteLink, null);
    assert.equal(linkShown("https://chat.whatsapp.com/Kx4vQ9"), "chat.whatsapp.com/Kx4vQ9");
});

test("a group the platform gave no title is named by a short id", () => {
    assert.equal(groupTitle(group()), "Design partners");
    assert.equal(groupTitle(group({ title: null })), "Group 7f3c9a");
});

test("says who answers people outside the space, and never a person who does not", () => {
    const members: Member[] = [{ id: "m1", name: "Priya Shah", initials: "PS", kind: "person", role: "Member", can: "", userId: "priya-user" }];
    assert.equal(groupLine(group(), "Marketing", "me"), "Priya answers for people outside Marketing");
    assert.equal(groupLine(group({ owner: { userId: "me", name: "Deepak" } }), "Marketing", "me"), "You answer for people outside Marketing");
    assert.equal(groupLine(group({ owner: { userId: "priya-user", name: null } }), "Marketing", "me", members), "Priya Shah answers for people outside Marketing");
    assert.equal(groupLine(group({ owner: { userId: "gone", name: null } }), "Marketing", "me"), "Someone in Marketing answers for people outside Marketing");
    // Nobody behind the switch, or the switch off: nobody is answering, and the first reason is said.
    assert.equal(groupLine(group({ owner: null, welcomesOutsiders: false }), "Marketing", "me"), "Nobody answers for people outside Marketing yet");
    assert.equal(groupLine(group({ answersOutsiders: false, welcomesOutsiders: false }), "Marketing", "me"), "People outside Marketing are not answered here");
    // The bot's own switch comes before anything the group says.
    assert.equal(groupLine(group({ botAnswersOutsiders: false, owner: null, welcomesOutsiders: false }), "Marketing", "me"), "Answering people outside Marketing is off for this bot");
    // Before the platform confirms it, that is the only thing worth saying.
    assert.equal(groupLine(group({ pending: true, externalId: null }), "Marketing", "me"), "Being created…");
});

test("offers to take a group over only to a reader who may, from somebody else or from nobody while it is on", () => {
    assert.equal(offersTakeOver(group(), "me"), true);
    assert.equal(offersTakeOver(group({ owner: { userId: "me", name: null } }), "me"), false);
    // Off with nobody: switching it on already makes you the one who answers.
    assert.equal(offersTakeOver(group({ owner: null, answersOutsiders: false, welcomesOutsiders: false }), "me"), false);
    assert.equal(offersTakeOver(group({ owner: null, welcomesOutsiders: false }), "me"), true);
    // Not the reader's to change, or a bot that answers nobody outside anyway.
    assert.equal(offersTakeOver(group({ canManage: false }), "me"), false);
    assert.equal(offersTakeOver(group({ botAnswersOutsiders: false, welcomesOutsiders: false }), "me"), false);
});

test("a reader who may not change a group is told who can", () => {
    const members: Member[] = [{ id: "m1", name: "Priya Shah", initials: "PS", kind: "person", role: "Member", can: "", userId: "priya-user" }];
    assert.equal(whoMayChange(group(), "Marketing", "Researcher", "me"), "Only Priya or an admin of Marketing can change it.");
    assert.equal(whoMayChange(group({ owner: { userId: "priya-user", name: null } }), "Marketing", "Researcher", "me", members), "Only Priya Shah or an admin of Marketing can change it.");
    assert.equal(whoMayChange(group({ owner: { userId: "gone", name: null } }), "Marketing", "Researcher", "me"), "Only the person who answers for it or an admin of Marketing can change it.");
    assert.equal(whoMayChange(group({ owner: null }), "Marketing", "Researcher", "me"), "Only someone who can change Researcher can change it.");
    assert.equal(whoMayChange(group({ owner: { userId: "me", name: null } }), "Marketing", "Researcher", "me"), "Only someone who can change Researcher can change it.");
});

test("a saved group replaces its row and nothing else", () => {
    const other = group({ id: "other", title: "Launch crew" });
    const saved = group({ owner: { userId: "me", name: null } });
    const next = withGroup([group(), other], saved);
    assert.equal(next[0], saved);
    assert.equal(next[1], other);
});

test("lists groups where a group can come to exist, and says how on each", () => {
    assert.equal(groupsSupported("TELEGRAM"), true);
    assert.equal(groupsSupported("whatsapp"), true);
    assert.equal(groupsSupported("RESEND"), false);
    assert.equal(groupsSupported("SLACK"), false);
    assert.equal(noGroupsYet("TELEGRAM", "Lem"), "Add Lem to a Telegram group and it shows up here.");
    // A WhatsApp bot cannot be added to a group; it opens one when asked.
    assert.equal(noGroupsYet("WHATSAPP", "Lem"), "Ask Lem on WhatsApp to open a group and it shows up here.");
});
