import test from "node:test";
import assert from "node:assert/strict";
import { messageIds, runTouched } from "../src/thread/run-touched.ts";

const none = new Set<string>();

test("an answer with no tools touches nothing", () => {
    assert.equal(runTouched([{ id: "1", role: "user", text: "hi" }, { id: "2", role: "assistant", text: "hello" }], none), "nothing");
});

test("only the run's own messages count, not an earlier run's", () => {
    const earlier = [{ id: "1", role: "user" }, { id: "2", role: "tool", tool_name: "exec_command" }];
    const after = [...earlier, { id: "3", role: "user" }, { id: "4", role: "tool", tool_name: "read_file" }];
    assert.equal(runTouched(after, messageIds(earlier)), "nothing");
});

test("file writes refresh the files, and a command refreshes everything", () => {
    assert.equal(runTouched([{ role: "tool", tool_name: "write_file" }, { role: "tool", tool_name: "grep" }], none), "files");
    assert.equal(runTouched([{ role: "tool", tool_name: "write_file" }, { role: "tool", tool_name: "exec_command" }], none), "everything");
});

test("a tool nobody listed is assumed to change things", () => {
    assert.equal(runTouched([{ role: "tool", tool_name: "create_workflow" }], none), "everything");
});
