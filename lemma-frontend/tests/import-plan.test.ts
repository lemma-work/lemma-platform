import test from "node:test";
import assert from "node:assert/strict";
import {
    CREATE_INSTALLATION,
    actionLabel,
    askedConnectors,
    askedVariables,
    blockedBy,
    connectorChoices,
    defaultPick,
    contentsLine,
    groupSteps,
    humanize,
    installedApp,
    landingPath,
    needsAnswer,
    suggestedValue,
    teammateName,
} from "../src/site/import/plan.ts";
import type {
    ConnectorPlan,
    PlanStep,
    VariableSpec,
} from "../src/site/import-types.ts";

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
        { name: "triage_channel", kind: "free", description: null, required: true, default: null },
        { name: "slug", kind: "free", description: null, required: false, default: "queue" },
    ];
    assert.deepEqual(
        askedVariables(variables).map((v) => v.name),
        ["triage_channel", "slug"],
    );
});

test("an install lands on the app it brought, not behind it", () => {
    // The teammate's own page is where a teammate without an app belongs.
    assert.equal(landingPath("p-1", [step("AGENT", "triager"), step("TABLE", "t")]), "/t/p-1");
    assert.equal(installedApp([step("AGENT", "triager")]), null);

    // An APP step is named by the app's own directory, which is the segment a
    // workspace URL names it by.
    const steps = [step("AGENT", "triager"), step("APP", "queue"), step("APP", "board")];
    assert.equal(installedApp(steps), "queue");
    assert.equal(landingPath("p-1", steps), "/t/p-1/app/queue");
});

test("an id or an app name that needs escaping is escaped", () => {
    assert.equal(landingPath("p 1", [step("APP", "my app")]), "/t/p%201/app/my%20app");
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

function connector(extra: Partial<ConnectorPlan> = {}): ConnectorPlan {
    return {
        slug: "crm",
        connector: "hubspot",
        mode: "PER_MEMBER",
        required: false,
        purpose: null,
        visibility: "POD",
        status: "READY",
        installations: [{ auth_config_id: "hs-1", name: "hubspot", is_default: true }],
        auth_config_id: "hs-1",
        can_create_installation: false,
        account_options: [],
        used_by: [],
        message: null,
        ...extra,
    };
}

test("a per-member connector with one installation asks nothing", () => {
    const shared = connector({
        slug: "inbox",
        mode: "SHARED",
        status: "NEEDS_ACCOUNT",
    });
    assert.deepEqual(
        askedConnectors([connector(), shared]).map((c) => c.slug),
        ["inbox"],
    );
});

test("the defaults send nothing, and share no account", () => {
    const shared = connector({
        slug: "inbox",
        mode: "SHARED",
        account_options: [
            { account_id: "me", auth_config_id: "hs-1", label: "me@x" },
        ],
    });
    assert.deepEqual(connectorChoices([connector(), shared], {}), {});
    assert.deepEqual(
        connectorChoices([shared], { inbox: { installation: "hs-1", share: "me" } }),
        { inbox: { share_account_id: "me" } },
    );
});

test("a connector with no installation blocks until one is chosen to be made", () => {
    const missing = connector({
        installations: [],
        auth_config_id: null,
        status: "NEEDS_INSTALLATION",
        message: "Ask an admin",
    });
    assert.equal(blockedBy(missing, defaultPick(missing)), "Ask an admin");
    assert.equal(
        blockedBy(missing, { installation: CREATE_INSTALLATION, share: "" }),
        "Ask an admin",
    );
    const creatable = { ...missing, can_create_installation: true };
    assert.equal(
        blockedBy(creatable, { installation: CREATE_INSTALLATION, share: "" }),
        null,
    );
    assert.deepEqual(
        connectorChoices([creatable], {
            crm: { installation: CREATE_INSTALLATION, share: "" },
        }),
        { crm: { create_installation: true } },
    );
});

test("a connector the pod already has is kept; a clashing slug blocks", () => {
    const kept = connector({ status: "EXISTS" });
    const clash = connector({ slug: "x", status: "CONFLICT", message: "taken" });
    assert.equal(blockedBy(kept, defaultPick(kept)), null);
    assert.equal(blockedBy(clash, defaultPick(clash)), "taken");
    assert.deepEqual(connectorChoices([kept], {}), {});
});
