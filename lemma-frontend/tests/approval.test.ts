import test from "node:test";
import assert from "node:assert/strict";
import {
    HOST_PERMISSION_WINDOW_MS,
    hostPermissionExpired,
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

/* A coding agent's permission request, as the backend writes it. */
function hostRequest(options: { option_id: string; kind: string; name: string }[]) {
    return {
        title: "Run ls -la",
        reason: "This request controls the local agent's access on this computer.",
        tool_name: "exec_command",
        agent_host_permission: { request_id: "r1", options, input: { cmd: "ls -la" } },
    };
}

test("a coding agent is offered 'for this conversation' only when it can keep it", () => {
    const once = approvalDetails(hostRequest([{ option_id: "a", kind: "allow_once", name: "Allow" }]));
    assert.equal(once.hostPermission, true);
    assert.equal(once.canApproveForSession, false);
    /* The generic host message is not the request; what it would run is. */
    assert.doesNotMatch(once.request, /controls the local agent/);
    assert.deepEqual(once.params, [{ name: "Cmd", value: "ls -la" }]);
    assert.equal(once.title, "Run ls -la");

    const always = approvalDetails(hostRequest([
        { option_id: "a", kind: "allow_once", name: "Allow" },
        { option_id: "b", kind: "allow_always", name: "Always allow ls" },
    ]));
    assert.equal(always.canApproveForSession, true);
    assert.equal(always.sessionLabel, "Always allow ls");

    /* Lemma's own approvals keep offering it. */
    assert.equal(approvalDetails({ tool_name: "files_delete", args: {} }).canApproveForSession, true);
});

test("a coding agent's request expires with its window or its run", () => {
    const details = approvalDetails(hostRequest([]));
    const asked = Date.parse("2026-09-15T12:00:00Z");
    assert.equal(hostPermissionExpired(details, { askedAtMs: asked, nowMs: asked + 60_000, runEnded: false }), false);
    assert.equal(hostPermissionExpired(details, { askedAtMs: asked, nowMs: asked + HOST_PERMISSION_WINDOW_MS, runEnded: false }), true);
    assert.equal(hostPermissionExpired(details, { askedAtMs: asked, nowMs: asked, runEnded: true }), true);
    /* An ordinary approval ends its run on purpose, and waits for as long as it takes. */
    const lemma = approvalDetails({ tool_name: "files_delete", args: {} });
    assert.equal(hostPermissionExpired(lemma, { askedAtMs: asked, nowMs: asked + 10 * HOST_PERMISSION_WINDOW_MS, runEnded: true }), false);
});
