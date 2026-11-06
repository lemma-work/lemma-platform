import test from "node:test";
import assert from "node:assert/strict";
import { stepLine, stepNow, stepsSaid, subjectOf } from "../src/thread/step-groups.ts";
import { buildTurns, commentOf } from "../src/thread/turns.ts";
import type { Note } from "../src/thread/turns.ts";

const tool = (key: string, label = key, extra: Partial<Note> = {}): Note => ({ kind: "tool", key, label, detail: "", ...extra });
const thought: Note = { kind: "thought", label: "Thought", detail: "hmm" };

test("a run of steps is said the way a person reports work, not as a list of tool names", () => {
    assert.equal(stepsSaid([tool("exec_command"), tool("exec_command"), tool("shell"), tool("exec_command")]), "Ran 4 commands", "tools that read the same way are one phrase");
    assert.equal(stepsSaid([tool("exec_command")]), "Ran a command");
    assert.equal(stepsSaid([tool("read_file"), thought, tool("web_search")]), "Read a file, searched the web", "thinking is part of the work, not a thing it did");
    assert.equal(stepsSaid([tool("write_file"), tool("exec_command")]), "Wrote a file, ran a command");
});

test("one step the agent described is said in its own words", () => {
    assert.equal(stepsSaid([tool("edit_file", "Edit file", { detail: "Made sweep take clone paths", said: true })]), "Made sweep take clone paths");
    assert.equal(stepsSaid([tool("edit_file", "Edit file", { detail: "a.py" })]), "Edited a file", "a summary of its arguments is not its words");
});

test("a tool the table does not know keeps its own name, with how many times", () => {
    assert.equal(stepsSaid([tool("", "Create issue · github"), tool("", "Create issue · github")]), "Create issue · github ×2");
    assert.equal(stepsSaid([tool("exec_command"), tool("", "API call")]), "Ran a command, API call", "an acronym is not lower-cased");
});

test("thinking alone is a thought, and a long run trails off", () => {
    assert.equal(stepsSaid([thought, thought]), "Thought");
    assert.equal(stepsSaid([]), "");
    assert.equal(
        stepsSaid([tool("exec_command"), tool("read_file"), tool("web_search"), tool("view_image")]),
        "Ran a command, read a file, searched the web, and more",
    );
});

test("what a live run is doing is said in the present: its own sentence, else the step under way", () => {
    const running = { running: true };
    assert.equal(stepNow([tool("exec_command", "Exec command", { detail: "Listing the failing CI checks", said: true, ...running })]), "Listing the failing CI checks");
    assert.equal(stepNow([tool("exec_command", "Exec command", { detail: "gh pr checks", ...running })]), "Running a command");
    assert.equal(stepNow([tool("web_search", "Web search", running)]), "Searching the web");
    assert.equal(stepNow([tool("", "Create issue · github", running)]), "Create issue · github", "a tool the table does not know keeps its name");
    assert.equal(stepNow([]), "Working");
});

test("between steps a live run is thinking, never on a step it has finished", () => {
    const said = tool("web_search", "Web search", { detail: "Looking for the launch date", said: true, running: true });
    /* Only the latest step counts: an earlier sentence is about work that is over. */
    assert.equal(stepNow([said, tool("web_fetch", "Web fetch", { running: true })]), "Reading a web page");
    assert.equal(stepNow([tool("web_search", "Web search")]), "Thinking", "returned");
    assert.equal(stepNow([tool("web_search", "Web search", { running: true }), thought]), "Thinking");
});

test("an opened run is one line per step: what it did, and what on", () => {
    assert.deepEqual(stepLine(tool("exec_command", "Exec command", { detail: "gh pr checks" })), { what: "Ran a command", on: "gh pr checks" });
    assert.deepEqual(stepLine(tool("exec_command", "Exec command", { detail: "Listing the failing CI checks", said: true })), { what: "Listing the failing CI checks", on: "" });
    assert.deepEqual(stepLine(tool("", "Create issue · github", { detail: "title=Bug" })), { what: "Create issue · github", on: "title=Bug" });
    assert.deepEqual(stepLine({ kind: "thought", label: "Thought", detail: "\nThe user wants the date.\nCheck the blog first." }), { what: "Thought", on: "The user wants the date." });
});

test("a step is said by what it was done to whenever it says, not as a kind of step", () => {
    assert.equal(subjectOf("read_file", { path: "/memory/lemma/design-direction.md" }), "design-direction.md");
    assert.equal(subjectOf("web_fetch", { url: "https://www.nodejs.org/api/test.html" }), "nodejs.org");
    assert.equal(subjectOf("web_search", { query: "AI agents  enterprise" }), "AI agents enterprise");
    assert.equal(subjectOf("bash", { command: "gh pr checks 1187" }), "gh pr checks 1187");
    assert.equal(subjectOf("execute_python", { code: "print(1)" }), "", "a tool not said that way has no subject");

    const read = tool("read_file", "Read file", { detail: "/memory/lemma/design-direction.md", subject: "design-direction.md" });
    assert.deepEqual(stepLine(read), { what: "Read design-direction.md", on: "" });
    assert.equal(stepNow([{ ...read, running: true }]), "Reading design-direction.md");
    assert.equal(stepsSaid([read]), "Read design-direction.md");
    const search = (query: string) => tool("web_search", "Web search", { subject: query });
    assert.equal(stepNow([{ ...search("AI agents"), running: true }]), "Searching the web for “AI agents”");
    assert.equal(
        stepsSaid([search("a"), search("b"), tool("web_fetch", "Web fetch", { subject: "nodejs.org" })]),
        "Searched the web 2 times, read nodejs.org",
        "counted when done more than once, named when once",
    );
});

test("the steps of a run carry what they were done to, and a command its own description", () => {
    const [turn] = buildTurns([
        { sequence: 1, role: "user", text: "go" },
        { sequence: 2, kind: "TOOL_CALL", tool_name: "pod_read_file", tool_call_id: "r1", tool_args: { path: "/memory/lemma/design-direction.md" } },
        { sequence: 3, kind: "TOOL_CALL", tool_name: "Bash", tool_call_id: "b1", tool_args: { command: "gh pr checks", description: "List the failing CI checks" }, metadata: { tool_source: "native" } },
    ]);
    assert.equal(stepLine(turn.notes[0]).what, "Read design-direction.md");
    assert.equal(stepLine(turn.notes[1]).what, "List the failing CI checks");
    assert.equal(commentOf({ description: "an issue body" }, "create_issue"), "", "another tool's description is not the agent talking");
});
