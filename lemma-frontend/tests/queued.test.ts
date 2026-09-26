import test from "node:test";
import assert from "node:assert/strict";
import { buildTurns, type RawMessage } from "../src/thread/turns.ts";
import { composerActions, inDeliveryOrder, isQueued, isWithdrawable, splitQueued, withdrawFailure } from "../src/thread/queued.ts";

/** Talking to a teammate while it works.
 *
 *  A message sent mid-run is queued until something delivers it. These pin
 *  where it is drawn while it waits, when it can be taken back, and where it
 *  lands once delivered -- the three things that read wrong when a queued
 *  message was simply drawn at its sequence. */

const RUN = "run-1";
const FOLLOW_UP = "run-2";
const at = (seconds: number) => new Date(1_700_000_000_000 + seconds * 1000).toISOString();

function message(partial: Partial<RawMessage> & { sequence: number }): RawMessage {
    return { id: "m" + partial.sequence, created_at: at(partial.sequence), agent_run_id: RUN, ...partial };
}

const asked = message({ sequence: 1, role: "user", text: "Refactor the parser." });
const working = message({ sequence: 2, role: "assistant", text: "Reading the grammar." });
const aside = message({
    sequence: 3,
    role: "user",
    text: "Keep the old API.",
    metadata: { during_active_run: true },
});
const done = message({ sequence: 4, role: "assistant", text: "Parser refactored." });

test("a message sent mid-run waits above the composer, not inside the answer", () => {
    const { transcript, queued } = splitQueued([asked, working, aside, done], true);

    assert.deepEqual(queued, [{ id: "m3", text: "Keep the old API.", withdrawable: true }]);
    /* Drawn inline it opened a turn of its own, and "Parser refactored." --
       an answer to the first message -- read as the reply to it. */
    const turns = buildTurns(transcript);
    assert.equal(turns.length, 1);
    assert.equal(turns[0].human?.text, "Refactor the parser.");
});

test("once a run takes it in, it is part of the conversation", () => {
    const heard = { ...aside, metadata: { during_active_run: true, steered_into_run: RUN } };
    const { transcript, queued } = splitQueued([asked, working, heard], true);

    assert.deepEqual(queued, []);
    assert.equal(buildTurns(transcript).length, 2);
});

test("with nothing running there is nothing to wait for", () => {
    /* Stopped before anyone read it. A tray promising it will be heard would
       be a promise nothing keeps. */
    const { transcript, queued } = splitQueued([asked, working, aside], false);

    assert.deepEqual(queued, []);
    assert.equal(transcript.length, 3);
});

test("a message on its way to a local agent's turn cannot be taken back", () => {
    const going = { ...aside, metadata: { during_active_run: true, steer_dispatched_at: at(3) } };
    assert.ok(isQueued(going));
    assert.ok(!isWithdrawable(going));

    /* The agent's turn ended first: merely queued again, for the next turn. */
    const bounced = { ...going, metadata: { ...going.metadata, steer_undelivered: "turn_ended" } };
    assert.ok(isWithdrawable(bounced));

    assert.deepEqual(splitQueued([asked, going], true).queued, [
        { id: "m3", text: "Keep the old API.", withdrawable: false },
    ]);
});

test("a message taken back here is gone before the server's list says so", () => {
    const { transcript, queued } = splitQueued([asked, aside], true, new Set(["m3"]));
    assert.deepEqual(queued, []);
    assert.deepEqual(transcript.map((item) => item.id), ["m1"]);
});

test("the first message of a run and the assistant's words are never queued", () => {
    assert.ok(!isQueued(asked));
    assert.ok(!isQueued({ ...working, metadata: { during_active_run: true } }));
});

test("a follow-up turn's messages are drawn where that turn begins", () => {
    const claimed = (item: RawMessage) => ({
        ...item,
        metadata: { during_active_run: true, steered_into_run: FOLLOW_UP },
    });
    const second = message({ sequence: 4, role: "user", text: "And add tests.", metadata: {} });
    const finished = message({ sequence: 5, role: "assistant", text: "Parser refactored." });
    const followUp = message({
        sequence: 6,
        role: "assistant",
        text: "Kept the old API and added tests.",
        agent_run_id: FOLLOW_UP,
    });

    const ordered = inDeliveryOrder([asked, working, claimed(aside), claimed(second), finished, followUp]);

    assert.deepEqual(
        ordered.map((item) => item.text),
        [
            "Refactor the parser.",
            "Reading the grammar.",
            "Parser refactored.",
            "Keep the old API.",
            "And add tests.",
            "Kept the old API and added tests.",
        ],
    );
    const turns = buildTurns(ordered);
    assert.equal(turns[0].items.at(-1)?.kind, "text");
    assert.equal(turns.at(-1)?.human?.text, "And add tests.");
});

test("claimed by a follow-up that has not spoken yet, they are the newest thing", () => {
    const claimed = { ...aside, metadata: { during_active_run: true, steered_into_run: FOLLOW_UP } };
    const ordered = inDeliveryOrder([asked, working, claimed, done]);
    assert.deepEqual(ordered.map((item) => item.id), ["m1", "m2", "m4", "m3"]);
});

test("a message steered into the run already going stays where it was heard", () => {
    const heard = { ...aside, metadata: { during_active_run: true, steered_into_run: RUN } };
    const ordered = inDeliveryOrder([asked, working, heard, done]);
    assert.deepEqual(ordered.map((item) => item.id), ["m1", "m2", "m3", "m4"]);
});

test("while a run is going, Send sits beside Stop as soon as there is something to send", () => {
    assert.deepEqual(composerActions(true, false), { stop: true, send: false });
    assert.deepEqual(composerActions(true, true), { stop: true, send: true });
    assert.deepEqual(composerActions(false, false), { stop: false, send: true });
    assert.deepEqual(composerActions(false, true), { stop: false, send: true });
});

test("only a conflict says the teammate already has a message being taken back", () => {
    assert.equal(withdrawFailure({ status: 409 }, "Ada"), "Ada already has that message.");
    for (const problem of [{ status: 503 }, new TypeError("Failed to fetch"), "offline", null]) {
        assert.equal(withdrawFailure(problem, "Ada"), "Could not take that message back. Try again.");
    }
});
