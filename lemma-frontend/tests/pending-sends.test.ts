import test from "node:test";
import assert from "node:assert/strict";
import { buildTurns, type RawMessage } from "../src/thread/turns.ts";
import { splitQueued } from "../src/thread/queued.ts";
import {
    CLIENT_MESSAGE_KEY,
    hasArrived,
    isSending,
    pendingQueued,
    pendingSend,
    stillPending,
    withPending,
    type PendingSend,
} from "../src/thread/pending-sends.ts";

/** A message on screen before the server has it.
 *
 *  Sending used to show nothing until the server sent the message back on the
 *  run's stream. These pin what is drawn in the meantime, how the server's copy
 *  takes its place without the turn being redrawn as a different one, and what
 *  a refused send leaves behind. */

const at = (seconds: number) => new Date(1_700_000_000_000 + seconds * 1000).toISOString();

const earlier: RawMessage[] = [
    { id: "u1", role: "user", text: "Summarise the week.", sequence: 1, created_at: at(1) },
    { id: "a1", role: "assistant", text: "Three launches, one outage.", sequence: 2, created_at: at(2) },
];

function sending(text: string, clientId = "c-1", messages = earlier): PendingSend {
    return pendingSend(text, "send", messages, clientId);
}

function echo(text: string, clientId: string | null, sequence = 3, id = "u2"): RawMessage {
    return {
        id,
        role: "user",
        text,
        sequence,
        created_at: at(sequence),
        metadata: clientId ? { [CLIENT_MESSAGE_KEY]: clientId } : null,
    };
}

test("a sent message is drawn at once, after everything the server holds", () => {
    const shown = withPending(earlier, [sending("And the outage?")]);
    const turns = buildTurns(shown);

    assert.equal(turns.length, 2);
    assert.equal(turns[1].human?.text, "And the outage?");
    assert.equal(turns[1].human?.pending, "sending");
    assert.ok(isSending([sending("And the outage?")]), "the transcript should say the teammate is on it");
});

test("the server's copy takes its place in the same turn, not a new one", () => {
    // Keyed by the id they share, the row is not remounted when one replaces
    // the other -- and nothing is drawn twice.
    const before = buildTurns(withPending(earlier, [sending("And the outage?")]));
    const after = buildTurns(withPending([...earlier, echo("And the outage?", "c-1")], [sending("And the outage?")]));

    assert.equal(after.length, 2);
    assert.equal(after[1].id, before[1].id);
    assert.equal(after[1].human?.pending, undefined);
    assert.equal(after[1].human?.id, "u2");
});

test("two sends of the same words are two messages", () => {
    // "yes", twice, is the case text matching gets wrong: the first echo would
    // settle both.
    const first = sending("yes", "c-1");
    const second = sending("yes", "c-2");
    const held = [...earlier, echo("yes", "c-1")];

    assert.deepEqual(stillPending([first, second], held).map((one) => one.clientId), ["c-2"]);
});

test("a server that does not echo the id is still matched, but only by a new message", () => {
    const going = sending("Check the logs.");
    // An old message saying the same thing is not this one's echo.
    const repeated = { ...earlier[0], text: "Check the logs." };
    assert.equal(hasArrived(pendingSend("Check the logs.", "send", [repeated], "c-9"), [repeated]), false);
    // Attachments append their references, so the echo starts with what was typed.
    assert.equal(hasArrived(going, [...earlier, echo("Check the logs.\n\nAttached: /files/log.txt", null)]), true);
});

test("a refused send stays where it was written, marked as not sent", () => {
    const refused: PendingSend = { ...sending("Ship it."), state: "failed" };
    const turns = buildTurns(withPending(earlier, [refused]));

    assert.equal(turns[1].human?.pending, "failed");
    assert.equal(isSending([refused]), false, "nothing is working on a message that did not send");
});

test("with nothing pending the conversation is passed through untouched", () => {
    // Identity matters: the turns are memoised on this array.
    assert.equal(withPending(earlier, []), earlier);
    const settled = [sending("x")];
    assert.equal(stillPending(settled, earlier), settled);
});

test("a message said mid-run waits in the tray as sending until the server has it", () => {
    const steer = pendingSend("Keep the old API.", "steer", earlier, "c-7");

    assert.deepEqual(pendingQueued([steer], earlier), [{ id: "c-7", text: "Keep the old API.", withdrawable: false }]);
    assert.equal(withPending(earlier, [steer]), earlier, "a steer is not a turn of its own");

    const queuedEcho = { ...echo("Keep the old API.", "c-7"), metadata: { [CLIENT_MESSAGE_KEY]: "c-7", during_active_run: true } };
    const held = [...earlier, queuedEcho];
    assert.deepEqual(pendingQueued([steer], held), []);
    // The server's copy is in the tray in its place, and can be taken back.
    assert.deepEqual(splitQueued(held, true).queued.map((one) => one.withdrawable), [true]);
});
