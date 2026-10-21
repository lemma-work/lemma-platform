import test from "node:test";
import assert from "node:assert/strict";
import { deliveryNotes, sayReason } from "../src/channels/deliveries.ts";
import { readChannelDetail, readLine } from "../src/channels/model.ts";

/** Under the sender's own line: where it went outside, and what became of
 *  each send -- sent, failed and why, skipped and why, with Retry where a
 *  send can be tried again. The line carries it (`delivered`). */

const places = readChannelDetail({
    id: "c", kind: "GROUP", created_at: "",
    source_places: [{ id: "s-slack", platform: "SLACK", title: "#launch" }, { id: "s-wa", platform: "whatsapp" }],
})!.places;

const mine = readLine({
    id: "l1", seq: 1, sender: { kind: "MEMBER", id: "me" }, text: "hello", created_at: "",
    delivered: [
        { id: "d1", source_id: "s-slack", platform: "SLACK", status: "SENT" },
        { id: "d2", source_id: "s-wa", platform: "WHATSAPP", status: "FAILED", reason: "The number is not on WhatsApp." },
        { id: "d3", source_id: "s-gone", platform: "TELEGRAM", status: "SKIPPED", reason: "outside_window" },
        { id: "d4", source_id: "s-wa", platform: "WHATSAPP", status: "DEAD", reason: "timeout" },
        { id: "d5", source_id: "s-slack", status: "PENDING" },
        { id: "bad", status: "LOST" },
    ],
})!;

test("a line reads where it went, dropping a status it does not know; another's line carries none", () => {
    assert.deepEqual(mine.delivered?.map((one) => one.id), ["d1", "d2", "d3", "d4", "d5"]);
    assert.equal(readLine({ id: "l2", seq: 2, sender: {}, created_at: "" })!.delivered, null);
    assert.equal(readLine({ id: "l3", seq: 3, sender: {}, created_at: "", delivered: [] })!.delivered, null, "nowhere outside is nothing to show");
    assert.equal(readLine({ id: "l4", seq: 4, sender: {}, created_at: "", deleted: true, delivered: [{ id: "d", status: "SENT" }] })!.delivered, null);
});

test("each send to a place says what became of it, and which can be retried", () => {
    assert.deepEqual(deliveryNotes(mine.delivered, places).map((note) => [note.tone, note.said, note.retry]), [
        ["sent", "Sent to #launch (Slack)", false],
        ["failed", "Failed to reach WhatsApp: The number is not on WhatsApp — trying again", true],
        ["skipped", "Not sent to Telegram: WhatsApp only allows a reply within a day of their last message", false],
        ["failed", "Couldn’t reach WhatsApp: timeout", true],
        ["pending", "Sending to #launch (Slack)…", false],
    ]);
    assert.deepEqual(deliveryNotes(null, places), []);
});

test("a reason is the executor's sentence, or an older row's code said plainly", () => {
    assert.equal(sayReason("That place is no longer this channel's."), "That place is no longer this channel's");
    assert.equal(sayReason("unsubscribed"), "they asked not to be written to there");
    assert.equal(sayReason("some_new_code"), "some new code");
    assert.equal(sayReason(null), null);
});
