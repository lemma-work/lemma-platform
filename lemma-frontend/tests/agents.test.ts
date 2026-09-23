import test from "node:test";
import assert from "node:assert/strict";
import {
    AGENT_EDIT, AGENT_READ, AGENT_REMOVE, AGENT_RUN, MAX_INSTRUCTION,
    agentChanges, agentProblems, agentRows, draftOfAgent, hasAgentChanges,
    may, readAgentDetail, readAgentRow, readSchema, runtimeLine, surfacesLost, whyNot,
} from "../src/data/agents.ts";

const OWNER = ["agent.read", "agent.execute", "agent.update", "agent.delete"];

test("the agent you talk to is listed first, and is not called a subordinate", () => {
    // `colleaguesFrom` drops it because the profile page is already about it.
    // This list is not, and hiding it here hides the only agent most pods have.
    const rows = agentRows({ items: [
        { name: "zebra" },
        { name: "pod_default" },
        { name: "alpha" },
    ] });

    assert.deepEqual(rows.map((row) => row.name), ["pod_default", "alpha", "zebra"]);
    assert.equal(rows[0].front, true);
    assert.equal(rows[0].label, "Lem", "not 'Pod Default' — the job title the product does not use");
    assert.equal(rows[1].front, false);
});

test("the default agent is recognised by kind as well as by name", () => {
    const [row] = agentRows({ items: [{ name: "assistant", kind: "POD_DEFAULT" }] });

    assert.equal(row.front, true);
});

test("a bare array is a list too", () => {
    // Half the endpoints answer `{items}` and half answer an array; a reader
    // that knows only one silently produces an empty page against the other.
    assert.equal(agentRows([{ name: "solo" }]).length, 1);
    assert.equal(agentRows({ items: [{ name: "solo" }] }).length, 1);
});

test("nothing in a malformed payload throws", () => {
    for (const junk of [null, undefined, 42, "agents", { items: "no" }, { items: [null, 7, "x"] }]) {
        assert.doesNotThrow(() => agentRows(junk), JSON.stringify(junk));
    }
    assert.equal(agentRows({ items: [null, 7] }).length, 2, "the items are still counted");
});

test("an agent with no name renders as a row that says so", () => {
    const rows = agentRows({ items: [{ description: "no name here" }, { name: "real" }] });
    const broken = rows.find((row) => row.broken);

    assert.ok(broken, "the row is kept, not dropped");
    assert.equal(broken.label, "Unreadable agent");
    assert.match(broken.blurb, /without a name/);
    assert.deepEqual(rows.map((row) => row.broken), [false, true], "readable rows come first");
});

test("toolsets become the words a person uses, through the one mapping", () => {
    const [row] = agentRows({ items: [{ name: "one", toolsets: ["WORKSPACE_CLI", "BROWSER"] }] });

    // "Computer", not "Shell": the words are one or two each so an icon can
    // carry them on the profile's capability strip, and `capabilityFor` in
    // `stage/colleagues.ts` is the one table both surfaces read.
    assert.deepEqual(row.can, ["Computer", "Browser"]);
    assert.deepEqual(row.toolsets, ["WORKSPACE_CLI", "BROWSER"], "the codes are kept as they are");
});

test("the list shape's own fields are read, not inferred", () => {
    // `AgentSummaryResponse` carries these three; the brief for this view
    // assumed only name/kind/description/icon_url/visibility/toolsets/metadata,
    // which would have cost a detail fetch per row to learn the same facts.
    const [row] = agentRows({ items: [{
        name: "typed",
        allowed_actions: ["Agent.Read", "agent.execute"],
        takes_input: true,
        has_pinned_runtime: true,
        updated_at: "2026-09-01T10:00:00Z",
    }] });

    assert.deepEqual(row.actions, ["agent.read", "agent.execute"], "case-folded, so a compare works");
    assert.equal(row.takesInput, true);
    assert.equal(row.pinnedRuntime, true);
    assert.equal(row.updated, "2026-09-01T10:00:00Z");
});

test("the blurb is one sentence, and falls back to the instruction", () => {
    const [described] = agentRows({ items: [{ name: "a", description: "Plans meals. Then shops." }] });
    assert.equal(described.blurb, "Plans meals.");

    const [bare] = agentRows({ items: [{ name: "b", instruction: "You file receipts. Carefully." }] });
    assert.equal(bare.blurb, "You file receipts.");
});

/* ── what you are allowed to do ────────────────────────────────── */

test("the pod's own assistant reports edit and delete, and cannot be edited or deleted", () => {
    // This is the one that matters. `allowed_actions` is projected from
    // permissions alone and never consults `kind`, so an owner sees all four
    // on the default agent — but `_refuse_pod_default` rejects both writes
    // before the permission check, with a 400 rather than a 403.
    const front = { actions: OWNER, front: true, broken: false, label: "Lem" };

    assert.equal(may(front, AGENT_READ), true);
    assert.equal(may(front, AGENT_RUN), true);
    assert.equal(may(front, AGENT_EDIT), false, "the API refuses this whatever the field says");
    assert.equal(may(front, AGENT_REMOVE), false);
    assert.match(whyNot(front, AGENT_EDIT), /talk to it/);
    assert.match(whyNot(front, AGENT_REMOVE), /It is the pod/);
});

test("an ordinary agent is edited and deleted exactly as far as the field allows", () => {
    const full = { actions: OWNER, front: false, broken: false, label: "Chef" };
    const reader = { actions: ["agent.read"], front: false, broken: false, label: "Chef" };

    assert.equal(may(full, AGENT_EDIT), true);
    assert.equal(may(full, AGENT_REMOVE), true);
    assert.equal(whyNot(full, AGENT_EDIT), "", "nothing to say when it is on offer");

    assert.equal(may(reader, AGENT_EDIT), false);
    assert.equal(may(reader, AGENT_REMOVE), false);
    assert.match(whyNot(reader, AGENT_EDIT), /read Chef, not change it/);
});

test("nothing is offered on a row with no name", () => {
    const broken = { actions: OWNER, front: false, broken: true, label: "Unreadable agent" };

    for (const action of [AGENT_READ, AGENT_RUN, AGENT_EDIT, AGENT_REMOVE]) {
        assert.equal(may(broken, action), false, action);
    }
    assert.match(whyNot(broken, AGENT_EDIT), /no name/);
});

/* ── the detail ────────────────────────────────────────────────── */

test("the detail reads the runtime, the schemas and the grants", () => {
    const detail = readAgentDetail({
        name: "researcher",
        kind: "USER",
        description: "Reads the web.",
        instruction: "You are a researcher.\n\nAlways cite.",
        toolsets: ["WEB_SEARCH"],
        allowed_actions: ["agent.read", "agent.update"],
        agent_runtime: { profile_id: "anthropic-key", model_name: "sonnet" },
        input_schema: { type: "object", properties: { topic: {}, depth: {} }, required: ["topic"] },
        output_schema: { type: "object", properties: { summary: {} } },
        permissions: { grants: [
            { resource_type: "DATASTORE_TABLE", resource_name: "leads", permission_ids: ["record.read"] },
            { resource_type: "AGENT", permission_ids: ["agent.read"] },
        ] },
    });

    assert.equal(detail.instruction, "You are a researcher.\n\nAlways cite.");
    assert.deepEqual(detail.runtime, { profile: "anthropic-key", model: "sonnet" });
    assert.equal(runtimeLine(detail), "anthropic-key · sonnet");
    assert.deepEqual(detail.input?.fields, ["topic", "depth"]);
    assert.deepEqual(detail.input?.required, ["topic"]);
    assert.equal(detail.output?.opaque, false);
    assert.deepEqual(detail.grants.map((grant) => grant.name), ["leads"], "a grant with no name is dropped");
});

test("a runtime profile with no model named is still a pinned runtime", () => {
    // `AgentRuntimeConfig` serializes `model_name` away when it is unset, so a
    // missing key and an empty string are the same fact: the profile decides.
    const detail = readAgentDetail({ name: "a", agent_runtime: { profile_id: "house" } });

    assert.deepEqual(detail.runtime, { profile: "house", model: "" });
    assert.equal(runtimeLine(detail), "house");
});

test("no runtime pinned reads as no runtime, not as a blank one", () => {
    assert.equal(readAgentDetail({ name: "a" }).runtime, null);
    assert.equal(runtimeLine({ runtime: null }), "");
});

test("a schema this cannot name is opaque, never absent", () => {
    // "Takes input, shape not shown here" is true. "Takes no input" is not.
    assert.equal(readSchema(undefined), null);
    assert.equal(readSchema({ $ref: "#/somewhere" })?.opaque, true);
    assert.equal(readSchema({ type: "object", properties: { a: {} } })?.opaque, false);
});

test("the detail's blurb falls back to its instruction", () => {
    const detail = readAgentDetail({ name: "a", instruction: "You do one thing. Well." });

    assert.equal(detail.blurb, "You do one thing.");
});

test("a malformed detail is a detail, not an exception", () => {
    for (const junk of [null, undefined, "agent", 12, { agent_runtime: "no", permissions: 4 }]) {
        assert.doesNotThrow(() => readAgentDetail(junk), JSON.stringify(junk));
    }
    assert.equal(readAgentDetail(null).broken, true);
});

/* ── editing ───────────────────────────────────────────────────── */

test("a PATCH carries only what changed", () => {
    const before = draftOfAgent(readAgentDetail({
        name: "chef", description: "Plans meals.", instruction: "You plan meals.",
    }));

    assert.deepEqual(agentChanges(before, before), {}, "an untouched form sends nothing");
    assert.equal(hasAgentChanges(before, before), false);

    assert.deepEqual(
        agentChanges(before, { ...before, instruction: "You plan meals and shop." }),
        { instruction: "You plan meals and shop." },
        "the description is not resent with it",
    );
});

test("a cleared description is an explicit null, so it actually clears", () => {
    const before = { description: "Plans meals.", instruction: "You plan meals." };

    assert.deepEqual(agentChanges(before, { ...before, description: "   " }), { description: null });
});

test("an emptied instruction is not sent, because the API will not take it", () => {
    // `UpdateAgentRequest.instruction` has min_length=1; sending "" is a 422,
    // and the form should say so rather than find out.
    const before = { description: "", instruction: "You plan meals." };

    assert.deepEqual(agentChanges(before, { ...before, instruction: "  " }), {});
    assert.equal(agentProblems({ description: "", instruction: "  " }).instruction,
        "An agent has to say what it is for. This cannot be emptied.");
});

test("an instruction is a document, so its blank lines survive a round trip", () => {
    const written = "One.\n\n  Two, indented.\n";
    const draft = draftOfAgent(readAgentDetail({ name: "a", instruction: written }));

    assert.equal(draft.instruction, written, "not trimmed — trimming makes every load look like an edit");
    assert.equal(hasAgentChanges(draft, draft), false);
});

test("an instruction past the limit is refused before the round trip", () => {
    const over = "x".repeat(MAX_INSTRUCTION + 1);

    assert.match(agentProblems({ description: "", instruction: over }).instruction ?? "", /the limit is 60,000/);
    assert.equal(agentProblems({ description: "", instruction: "x".repeat(MAX_INSTRUCTION) }).instruction, undefined);
});

/* ── what deleting breaks ──────────────────────────────────────── */

test("deleting names the addresses that stop answering, matched the way the field is spelled", () => {
    // `teardown_agent_surfaces` runs before the row goes, so these are gone.
    // `Surface.agentName` is already humanized by `live.ts`, so the match is
    // against the row's label — against `invoice-filer` it never fires, and
    // the confirmation would promise nothing breaks every time.
    const surfaces = [
        { agentName: "Invoice filer", platform: "TELEGRAM", handle: "@filer_bot", active: true },
        { agentName: "Invoice filer", platform: "RESEND", handle: "filer@ops.example", active: false },
        { agentName: "Lem", platform: "SLACK", handle: "#general", active: true },
    ];

    const [row] = agentRows({ items: [{ name: "invoice-filer" }] });

    assert.equal(row.label, "Invoice filer");
    assert.deepEqual(surfacesLost(surfaces, row.label), [{ platform: "TELEGRAM", handle: "@filer_bot" }]);
    assert.deepEqual(surfacesLost(surfaces, row.name), [], "the row name is not what the field holds");
    assert.deepEqual(surfacesLost(surfaces, "Nobody"), []);
});

test("the four action ids are the backend's own", () => {
    // RESOURCE_ACTIONS[ResourceType.AGENT]. `agent.create` is deliberately not
    // here: creating is pod-scoped, so no row reports it.
    assert.deepEqual(
        [AGENT_READ, AGENT_RUN, AGENT_EDIT, AGENT_REMOVE],
        ["agent.read", "agent.execute", "agent.update", "agent.delete"],
    );
});

test("a row read on its own matches one read through the list", () => {
    const raw = { name: "chef", toolsets: ["POD"], allowed_actions: ["agent.read"] };

    assert.deepEqual(readAgentRow(raw), agentRows([raw])[0]);
});
