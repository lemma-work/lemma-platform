import test from "node:test";
import assert from "node:assert/strict";
import { previewOf, readChannel, readLine } from "../src/channels/model.ts";
import {
    REPORT_REASONS,
    blockLine,
    blockPath,
    blockable,
    blockedNote,
    cannotBlock,
    isServerId,
    reportBody,
    reportPath,
    reportTitle,
} from "../src/reports/model.ts";

/** Reporting what was said and blocking a person: what is sent, and what the
 *  reader is told. */

const ID = "0193b4f2-6c1d-7a3e-9f00-1234567890ab";

test("a report goes to the message it is about, with one of the server's reasons", () => {
    assert.equal(reportPath("pod", { kind: "reply", conversationId: "c", messageId: "m" }), "/pods/pod/conversations/c/messages/m/report");
    assert.equal(reportPath("pod", { kind: "line", channelId: "ch", lineId: "l" }), "/pods/pod/channels/ch/messages/l/report");
    assert.deepEqual(REPORT_REASONS.map((reason) => reason.id), ["OFFENSIVE", "HARMFUL", "SEXUAL", "SPAM", "OTHER"]);
    assert.deepEqual(reportBody("SPAM", "   "), { reason: "SPAM" });
    assert.deepEqual(reportBody("OTHER", "  rude  "), { reason: "OTHER", note: "rude" });
    assert.equal(reportBody("OTHER", "x".repeat(1500)).note?.length, 1000);
});

test("a person can be blocked; a Lemma, oneself and somebody nobody vouched for cannot", () => {
    assert.deepEqual(blockable({ kind: "CONTACT", id: "c1", name: "Sam" }, "me"), { kind: "CONTACT", id: "c1", name: "Sam" });
    assert.deepEqual(blockable({ kind: "MEMBER", id: "u2", name: null }, "me"), { kind: "MEMBER", id: "u2", name: "this person" });
    assert.equal(blockable({ kind: "MEMBER", id: "me", name: "Me" }, "me"), null);
    assert.equal(blockable({ kind: "AGENT", id: "a1", name: "Support" }, "me"), null);
    assert.equal(blockable({ kind: "CONTACT", id: null, name: "Sam" }, "me"), null);
    // Said in the row, rather than the button vanishing.
    assert.match(cannotBlock({ kind: "CONTACT", id: null }) ?? "", /didn’t confirm who they are/);
    assert.equal(cannotBlock({ kind: "AGENT", id: "a1" }), null);
    assert.equal(blockPath("pod", { kind: "CONTACT", id: "c1" }), "/pods/pod/blocks/CONTACT:c1");
});

test("the dialogs say what happens, plainly", () => {
    assert.equal(reportTitle({ kind: "reply", conversationId: "c", messageId: "m" }, "Support"), "Report this reply");
    assert.equal(reportTitle({ kind: "line", channelId: "c", lineId: "l" }, "Sam"), "Report Sam’s message");
    assert.match(blockLine({ kind: "CONTACT", id: "c1", name: "Sam" }, "Support"), /^You won’t see Sam’s messages in Support’s chats/);
    assert.equal(blockedNote("Sam"), "A message from Sam, hidden because you blocked them.");
    assert.equal(blockedNote("Sam", 3), "3 messages from Sam, hidden because you blocked them.");
});

test("only a server's id is reported: a row drawn before the server had it is not", () => {
    assert.equal(isServerId(ID), true);
    assert.equal(isServerId("s3"), false);
    assert.equal(isServerId("u0"), false);
});

test("a line from somebody the reader blocked keeps nothing of what it said", () => {
    const line = readLine({ id: ID, seq: 4, sender: { kind: "CONTACT", id: "c1", name: "Sam" }, text: "rude", blocked: true, parts: [{ type: "link", url: "https://example.com" }] })!;
    assert.equal(line.blocked, true);
    assert.equal(line.text, null);
    assert.deepEqual(line.parts, []);
    const channel = readChannel({ id: "ch", kind: "GROUP", created_at: "", last_line: { sender: { kind: "CONTACT", id: "c1", name: "Sam" }, text: "", blocked: true, created_at: "" } })!;
    assert.equal(previewOf(channel, "me"), "Sam: a message you blocked");
});
