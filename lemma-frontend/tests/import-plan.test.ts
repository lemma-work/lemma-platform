import test from "node:test";
import assert from "node:assert/strict";
import {
    actionLabel,
    askedVariables,
    contentsLine,
    groupSteps,
    humanize,
    needsAnswer,
    suggestedValue,
    teammateName,
} from "../src/site/import/plan.ts";
import type { PlanStep, VariableSpec } from "../src/site/import-types.ts";

function step(kind: string, name: string, extra: Partial<PlanStep> = {}): PlanStep {
    return {
        index: 0,
        kind,
        name,
        action: "CREATE",
        destructive: false,
        detail: {},
        status: "PENDING",
        error: null,
        ...extra,
    };
}

test("steps group by kind, agents first, with grants folded away", () => {
    const groups = groupSteps([
        step("FILE", "notes.md"),
        step("AGENT_GRANTS", "triager"),
        step("AGENT", "triager"),
        step("AGENT", "fixer"),
        step("FUNCTION_GRANTS", "open_pr"),
        step("APP", "queue"),
    ]);
    assert.deepEqual(
        groups.map((g) => [g.label, g.steps.length]),
        [
            ["Agents", 2],
            ["App", 1],
            ["File", 1],
        ],
    );
});

test("a kind the page has never heard of still gets a readable group", () => {
    const [group] = groupSteps([step("KNOWLEDGE_BASE", "docs")]);
    assert.equal(group.label, "Knowledge base");
});

test("a destructive step says it replaces yours whatever its action", () => {
    assert.equal(actionLabel(step("TABLE", "t", { action: "UPDATE", destructive: true })), "Replaces yours");
    assert.equal(actionLabel(step("TABLE", "t", { action: "UPDATE" })), "Updates yours");
    assert.equal(actionLabel(step("TABLE", "t", { action: "SKIP" })), "Already there");
    assert.equal(actionLabel(step("TABLE", "t")), "New");
});

test("the installer is never asked about a variable that fills itself in", () => {
    const variables: VariableSpec[] = [
        { name: "owner", kind: "pod_member", description: null, required: false, default: null },
        { name: "slack_account", kind: "account", description: null, required: true, default: null, connector: "slack" },
        { name: "slug", kind: "free", description: null, required: false, default: "queue" },
    ];
    assert.deepEqual(
        askedVariables(variables).map((v) => v.name),
        ["slack_account", "slug"],
    );
});

test("names read as words", () => {
    assert.equal(humanize("slack_account"), "Slack account");
    assert.equal(humanize("triageChannel"), "Triage channel");
    assert.equal(teammateName("smart-inbox"), "Smart Inbox");
});

test("a name is filled in for the person rather than asked about", () => {
    const slug: VariableSpec = {
        name: "slug",
        kind: "free",
        description: null,
        required: true,
        default: null,
    };
    assert.equal(suggestedValue(slug, "Smart Inbox"), "smart-inbox");
    assert.equal(needsAnswer(slug, "Smart Inbox"), false);
});

test("a bundle's own default still wins over the teammate's name", () => {
    const variable: VariableSpec = {
        name: "slug",
        kind: "free",
        description: null,
        required: false,
        default: "queue",
    };
    assert.equal(suggestedValue(variable, "Smart Inbox"), "queue");
});

test("an account belongs to whoever is installing, so it is always asked", () => {
    const account: VariableSpec = {
        name: "slack_account",
        kind: "account",
        description: null,
        required: true,
        default: null,
        connector: "slack",
    };
    assert.equal(suggestedValue(account, "Smart Inbox"), "");
    assert.equal(needsAnswer(account, "Smart Inbox"), true);
});

test("a required name nothing can be derived from is still put in front of the person", () => {
    const variable: VariableSpec = {
        name: "slug",
        kind: "free",
        description: null,
        required: true,
        default: null,
    };
    assert.equal(suggestedValue(variable, "☕"), "");
    assert.equal(needsAnswer(variable, "☕"), true);
});

test("what a bundle brings reads as one line, not a checklist", () => {
    assert.equal(
        contentsLine(
            groupSteps([
                step("AGENT", "triager"),
                step("AGENT", "fixer"),
                step("TABLE", "tickets"),
                step("WORKFLOW", "route"),
            ]),
        ),
        "2 agents, 1 workflow and 1 table",
    );
    assert.equal(contentsLine(groupSteps([step("TABLE", "tickets")])), "1 table");
    assert.equal(
        contentsLine(groupSteps([step("TABLE", "a"), step("AGENT", "b")])),
        "1 agent and 1 table",
    );
    assert.equal(contentsLine([]), "");
});
