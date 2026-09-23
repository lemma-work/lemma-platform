import test from "node:test";
import assert from "node:assert/strict";
import {
    approvalDetails,
    interactionHeading,
    askQuestions,
    decisionLabel,
    isInteractionTool,
    resolvedDecision,
} from "../src/thread/approval.ts";

test("reads the call inside the envelope, not the envelope", () => {
    // Exactly the shape the backend sends: the tool actually waiting is in
    // `args.tool_name`, and its real arguments are nested one deeper.
    const details = approvalDetails({
        tool_name: "files_delete",
        title: "Delete muscle-based-robots.md from pod files",
        reason: "The pod refused the delete: DESTRUCTIVE_ACTION_REQUIRES_APPROVAL — deleting the memo we just wrote needs your authority.",
        args: { path: "/me/research/clone-alternative/muscle-based-robots.md", yes: true },
    });

    assert.equal(details.title, "Delete muscle-based-robots.md from pod files");
    assert.match(details.request, /DESTRUCTIVE_ACTION_REQUIRES_APPROVAL/);
    assert.equal(details.toolName, "files_delete");
    assert.deepEqual(details.params, [
        { name: "Path", value: "/me/research/clone-alternative/muscle-based-robots.md" },
        { name: "Yes", value: "true" },
    ]);
});

test("never shows the names of the envelope's own fields", () => {
    // The bug this replaced: a card headed "Run request_approval?" whose body
    // read "args, title, reason" — the field names, offered to somebody being
    // asked to authorise a deletion.
    const details = approvalDetails({ tool_name: "exec_command", args: { command: "ls -la" } });
    assert.equal(details.title, "Exec command");
    assert.deepEqual(details.params, [{ name: "Command", value: "ls -la" }]);
    for (const banned of ["args", "title", "reason"]) {
        assert.ok(!details.params.some(p => p.name.toLowerCase() === banned), banned + " leaked into the card");
    }
});

test("falls back to the approval's own text when the agent wrote nothing", () => {
    const details = approvalDetails({}, "Needs your decision");
    assert.equal(details.title, "Needs your decision");
    assert.deepEqual(details.params, []);
});

test("a nested object is summarised rather than dumped as JSON", () => {
    const details = approvalDetails({ tool_name: "t", args: { payload: { a: 1, b: 2, c: 3, d: 4 }, rows: [1, 2, 3] } });
    assert.deepEqual(details.params, [
        { name: "Payload", value: "{ a, b, c, … }" },
        { name: "Rows", value: "3 items" },
    ]);
});

test("at most four arguments reach the card", () => {
    const details = approvalDetails({ tool_name: "t", args: { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6 } });
    assert.equal(details.params.length, 4);
});

test("reads ask_user questions, and drops the ones it cannot answer", () => {
    const questions = askQuestions({
        questions: [
            {
                question: "Which account should this send from?",
                header: "Account",
                multi_select: false,
                options: [
                    { label: "work@example.invalid", description: "The shared inbox" },
                    { label: "me@example.invalid" },
                ],
            },
            // No options: nothing for a person to click, so nothing to draw.
            { question: "Anything else?", header: "Notes", options: [] },
        ],
    });

    assert.equal(questions.length, 1);
    assert.equal(questions[0].header, "Account");
    assert.equal(questions[0].multiSelect, false);
    assert.deepEqual(
        questions[0].options.map((option) => option.label),
        ["work@example.invalid", "me@example.invalid"],
    );
});

test("knows which tools stop the run, namespaced or not", () => {
    assert.equal(isInteractionTool("request_approval"), true);
    assert.equal(isInteractionTool("mcp__lemma__ask_user"), true);
    assert.equal(isInteractionTool("user_approval"), true);
    assert.equal(isInteractionTool("pod_write_file"), false);
    assert.equal(isInteractionTool(null), false);
});

test("names a decision the way the person who made it would", () => {
    assert.equal(decisionLabel("APPROVE_FOR_SESSION"), "Approved for this conversation");
    assert.equal(decisionLabel("APPROVE_ONCE"), "Approved once");
    assert.equal(decisionLabel("DENY"), "Denied");
    // A question was answered, not approved.
    assert.equal(decisionLabel("APPROVE_ONCE", "question"), "Answered");
    assert.equal(decisionLabel("DENY", "question"), "Skipped");
});

test("finds the decision wherever the backend put it", () => {
    assert.equal(resolvedDecision({ decision: "DENY" }), "DENY");
    assert.equal(resolvedDecision({ output: { decision: "APPROVE_ONCE" } }), "APPROVE_ONCE");
    assert.equal(resolvedDecision({}), "");
    assert.equal(resolvedDecision(null), "");
});

test("a nameless question is headed by who is asking, not by the word approval", () => {
    const details = approvalDetails({ questions: [] });
    assert.equal(details.title, "");
    assert.equal(interactionHeading("question", details.title, "Blogger", false), "Blogger needs your answer");
    assert.equal(interactionHeading("question", details.title, "Blogger", true), "Blogger asked you");
});

test("a heading the agent wrote survives both states", () => {
    assert.equal(interactionHeading("question", "Which quote opens?", "Blogger", false), "Which quote opens?");
    assert.equal(interactionHeading("approval", "Overwrite v3", "Blogger", true), "Overwrite v3");
});

test("a nameless approval still says approval, and never leaves a blank where a name goes", () => {
    assert.equal(interactionHeading("approval", "", "Blogger", false), "Blogger needs your approval");
    assert.equal(interactionHeading("approval", "", "  ", false), "Your teammate needs your approval");
});
