import { strict as assert } from "node:assert";
import test from "node:test";
import { changedLately, isMemoryPath, notedBy, notesFrom, topicOf } from "@/thread/memory-notes";
import { buildTurns } from "@/thread/turns";

/** What the teammate wrote down is read from files, never from the model
 *  describing itself. These pin the reading: which paths are memory, which
 *  writes count, and what the line under a reply is drawn from. */

test("memory lives under /memory and the agent folders under /me, and nowhere else", () => {
    assert.ok(isMemoryPath("/memory/pricing.md"));
    assert.ok(isMemoryPath("/memory/agents/pod-default/working-style.md"));
    assert.ok(isMemoryPath("/me/agents/pod-default/your-preferences.md"));
    assert.ok(isMemoryPath("/me/AGENTS.md"));
    assert.ok(!isMemoryPath("/me/notes.md"));
    assert.ok(!isMemoryPath("/memories/pricing.md"));
    assert.ok(!isMemoryPath("/pages/memory.md"));
});

test("a note's topic is its file name, said the way a person would", () => {
    assert.equal(topicOf("/memory/launch-checks.md"), "Launch checks");
    assert.equal(topicOf("/memory/brand_voice.md"), "Brand voice");
    assert.equal(topicOf("customerNames.md"), "Customer names");
});

test("the list is files only, without the indexes, newest first, and each note once", () => {
    const at = (day: number) => "2026-09-" + String(day).padStart(2, "0") + "T09:00:00Z";
    const notes = notesFrom([
        { private: false, items: [
            { id: "1", name: "AGENTS.md", kind: "file", path: "/memory/AGENTS.md", updated: at(30), detail: "" },
            { id: "2", name: "agents", kind: "folder", path: "/memory/agents", updated: at(30), detail: "" },
            { id: "3", name: "pricing.md", kind: "file", path: "/memory/pricing.md", updated: at(20), detail: "text/markdown", description: "Quotes go to Priya" },
            { id: "4", name: "voice.md", kind: "file", path: "/memory/voice.md", updated: at(25), detail: "text/markdown" },
        ] },
        { private: true, items: [
            { id: "5", name: "yours.md", kind: "file", path: "/me/agents/pod-default/yours.md", updated: at(10), detail: "" },
            { id: "3", name: "pricing.md", kind: "file", path: "/memory/pricing.md", updated: at(20), detail: "" },
        ] },
    ]);
    assert.deepEqual(notes.map((note) => note.topic), ["Voice", "Pricing", "Yours"]);
    assert.equal(notes[1].gloss, "Quotes go to Priya");
    /* A mime type is not a description: the gloss is empty rather than "text/markdown". */
    assert.equal(notes[0].gloss, "");
    assert.equal(notes[2].private, true);
});

test("the week's dot is the last seven days and never the future", () => {
    const now = Date.parse("2026-10-01T12:00:00Z");
    assert.ok(changedLately("2026-09-28T12:00:00Z", now));
    assert.ok(!changedLately("2026-09-20T12:00:00Z", now));
    assert.ok(!changedLately("2026-10-03T12:00:00Z", now));
    assert.ok(!changedLately("not a date", now));
});

test("only a memory write that came back successful is noted", () => {
    assert.deepEqual(notedBy("pod_write_file", { path: "/memory/pricing.md" }, { success: true, path: "/memory/pricing.md" }),
        { path: "/memory/pricing.md", topic: "Pricing", private: false });
    assert.deepEqual(notedBy("pod_edit_file", { path: "/me/agents/pod-default/yours.md" }, { success: true }),
        { path: "/me/agents/pod-default/yours.md", topic: "Yours", private: true });
    /* Not yet returned, failed, an index, somewhere else, another tool. */
    assert.equal(notedBy("pod_write_file", { path: "/memory/pricing.md" }, undefined), null);
    assert.equal(notedBy("pod_write_file", { path: "/memory/pricing.md" }, { success: false, error: "exists" }), null);
    assert.equal(notedBy("pod_write_file", { path: "/memory/AGENTS.md" }, { success: true }), null);
    assert.equal(notedBy("pod_write_file", { path: "/pages/plan.md" }, { success: true }), null);
    assert.equal(notedBy("pod_read_file", { path: "/memory/pricing.md" }, { content: "…" }), null);
});

test("a turn carries each note it wrote once, however many times it saved it", () => {
    const call = (id: string, sequence: number, path: string) => ({ id, role: "assistant", kind: "TOOL_CALL", sequence, tool_name: "pod_write_file", tool_call_id: id, tool_args: { path } });
    const back = (id: string, sequence: number) => ({ id: id + "r", role: "assistant", kind: "TOOL_RETURN", sequence, tool_call_id: id, tool_result: { success: true } });
    const turns = buildTurns([
        { id: "u", role: "user", kind: "TEXT", sequence: 1, text: "Quotes always go to Priya first." },
        call("a", 2, "/memory/pricing.md"), back("a", 3),
        call("b", 4, "/memory/pricing.md"), back("b", 5),
        call("c", 6, "/memory/AGENTS.md"), back("c", 7),
        { id: "t", role: "assistant", kind: "TEXT", sequence: 8, text: "Got it." },
    ]);
    assert.equal(turns.length, 1);
    assert.deepEqual(turns[0].noted, [{ path: "/memory/pricing.md", topic: "Pricing", private: false }]);
    /* The writes are still steps in the trace. */
    assert.equal(turns[0].notes.length, 3);
});
