import { displayAgentName, isPodDefaultAgent } from "./agent-names";
import { capabilities, firstSentence } from "@/stage/colleagues";

/** The agents behind a teammate, read off the wire.
 *
 *  Pure, and tested, for the reason `runtimes.ts` is: every field here is a
 *  wire field, and reading one that does not exist fails silently rather than
 *  loudly — a blank column of names, a capability list that is always empty, a
 *  Delete button on the one agent the API will never delete. Those are the
 *  mistakes a screenshot cannot catch.
 *
 *  Two shapes, because the API has two. `AgentSummaryResponse` is what the
 *  list returns and it is deliberately lean; `AgentDetailResponse` adds the
 *  instruction, the runtime, the schemas and the grants. So the list draws
 *  from the first and opening one costs a second request.
 */

/* ── what an action is called ───────────────────────────────────────
   `allowed_actions` carries permission ids, and these are the four the
   backend projects for an agent (`RESOURCE_ACTIONS[ResourceType.AGENT]`).
   Note `agent.create` is not among them: creating is pod-scoped, so no agent
   row ever reports whether you may make another one. */
export const AGENT_READ = "agent.read";
export const AGENT_RUN = "agent.execute";
export const AGENT_EDIT = "agent.update";
export const AGENT_REMOVE = "agent.delete";

/** How long an instruction may be, matching `MAX_AGENT_INSTRUCTION_CHARACTERS`.
 *  Checked here so a person who has just written 60k characters is told before
 *  the round trip rather than after it. */
export const MAX_INSTRUCTION = 60_000;

/** A row in the list. */
export interface AgentRow {
    /** The row name — the identifier every call is keyed by, never the label. */
    name: string;
    label: string;
    /** The one the person is actually talking to. It is not a subordinate, and
     *  a list that draws it like one is lying about who is answering. */
    front: boolean;
    /** What it is for, in one line: its description, or the first sentence of
     *  whatever else the payload offered. */
    blurb: string;
    /** Toolsets as words, via `capabilities` — the one mapping. */
    can: string[];
    toolsets: string[];
    visibility: string;
    iconUrl: string | null;
    actions: string[];
    /** `takes_input` — an agent with typed inputs is *called* with arguments;
     *  one without is *talked to*. The list says which, because "open its
     *  conversation" is the wrong offer for the first kind. */
    takesInput: boolean;
    /** `has_pinned_runtime` — whether it names its own model rather than
     *  taking the pod's. The config itself is on the detail. */
    pinnedRuntime: boolean;
    updated: string;
    /** Set when the item was not an agent shape at all. The row still draws;
     *  it just says so. */
    broken: boolean;
}

/** One field of a declared schema, flattened enough to list. */
export interface SchemaNote {
    fields: string[];
    required: string[];
    /** Set when there is a schema but nothing this reader could name in it. */
    opaque: boolean;
}

/** A grant the agent holds over something else in the pod. */
export interface AgentGrant {
    resource: string;
    name: string;
    permissions: string[];
}

export interface AgentDetail extends AgentRow {
    description: string;
    instruction: string;
    /** `agent_runtime`, when one is pinned. `model` is empty when the profile
     *  is named but the model is left to the profile's own default — the API
     *  drops `model_name` from the payload entirely in that case, so an empty
     *  string here and a missing key there are the same fact. */
    runtime: { profile: string; model: string } | null;
    input: SchemaNote | null;
    output: SchemaNote | null;
    grants: AgentGrant[];
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function list(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

function strings(value: unknown): string[] {
    return list(value).map(text).filter(Boolean);
}

function object(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
}

/** A list response, either shape. Same rule as `search/sources.ts`. */
function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    const items = object(value)?.items;
    return Array.isArray(items) ? items : [];
}

/** One item from the list endpoint.
 *
 *  Nothing here throws. An agent with no name cannot be opened, edited or
 *  deleted — there is no identifier to key any of it by — so it comes back
 *  marked rather than dropped: a pod showing four of its five agents with no
 *  sign of the fifth is worse than one showing a row that admits it.
 */
export function readAgentRow(raw: unknown): AgentRow {
    const item = object(raw) ?? {};
    const name = text(item.name);
    const kind = text(item.kind) || null;
    const front = isPodDefaultAgent(name, kind);
    const description = text(item.description);
    return {
        name,
        label: name ? displayAgentName(name, kind) : "Unreadable agent",
        front,
        blurb: name
            ? firstSentence(description || text(item.instruction))
            : "This row arrived without a name, so there is nothing to open.",
        can: capabilities(item.toolsets),
        toolsets: strings(item.toolsets),
        visibility: text(item.visibility).toUpperCase(),
        iconUrl: text(item.icon_url) || null,
        actions: strings(item.allowed_actions).map((one) => one.toLowerCase()),
        takesInput: item.takes_input === true,
        pinnedRuntime: item.has_pinned_runtime === true,
        updated: text(item.updated_at),
        broken: !name,
    };
}

/** Every agent in a pod, the one you talk to first.
 *
 *  Sorted rather than filtered — `colleaguesFrom` drops the default agent
 *  because the profile page is already about it, and this list is not. Hiding
 *  it here would hide the only agent most pods have.
 */
export function agentRows(raw: unknown): AgentRow[] {
    return itemsOf(raw)
        .map(readAgentRow)
        .sort((a, b) =>
            Number(b.front) - Number(a.front) ||
            Number(a.broken) - Number(b.broken) ||
            a.label.localeCompare(b.label));
}

/** What a declared schema asks for, named rather than counted.
 *
 *  Only the top level, and only `properties` — these are the schemas an agent
 *  builder writes, which are flat objects far more often than not. A schema
 *  this cannot read back is reported as opaque rather than as absent: "takes
 *  input, shape not shown here" is true, and "takes no input" would not be.
 */
export function readSchema(raw: unknown): SchemaNote | null {
    const schema = object(raw);
    if (!schema) return null;
    const properties = object(schema.properties);
    const fields = properties ? Object.keys(properties) : [];
    return {
        fields,
        required: strings(schema.required).filter((one) => !fields.length || fields.includes(one)),
        opaque: fields.length === 0,
    };
}

/** One agent from the detail endpoint. */
export function readAgentDetail(raw: unknown): AgentDetail {
    const item = object(raw) ?? {};
    const row = readAgentRow(item);
    const runtime = object(item.agent_runtime);
    const profile = text(runtime?.profile_id);
    const permissions = object(item.permissions);
    return {
        ...row,
        /* The detail carries the instruction, so the one-line blurb can come
           from it when there is no description — the list shape has no
           instruction to fall back to and leaves that line empty. */
        blurb: row.blurb || firstSentence(text(item.instruction)),
        description: text(item.description),
        instruction: typeof item.instruction === "string" ? item.instruction : "",
        runtime: profile ? { profile, model: text(runtime?.model_name) } : null,
        input: readSchema(item.input_schema),
        output: readSchema(item.output_schema),
        grants: list(permissions?.grants).map((entry) => {
            const grant = object(entry) ?? {};
            return {
                resource: text(grant.resource_type),
                name: text(grant.resource_name),
                permissions: strings(grant.permission_ids),
            };
        }).filter((grant) => grant.name),
    };
}

/** Whether an action is on offer, and it takes more than the permission.
 *
 *  `allowed_actions` is projected from permissions alone — it never consults
 *  `kind`. A pod owner holds `agent.update` and `agent.delete` over the pod's
 *  own assistant and the list says so, but `_refuse_pod_default` in the agent
 *  service rejects both before the permission is ever checked, with a 400
 *  rather than a 403. So the row that reports the most permission is the one
 *  row where editing and deleting are impossible, and a screen trusting the
 *  field alone offers two buttons that cannot work.
 */
export function may(agent: Pick<AgentRow, "actions" | "front" | "broken">, action: string): boolean {
    if (agent.broken) return false;
    if (agent.front && (action === AGENT_EDIT || action === AGENT_REMOVE)) return false;
    return agent.actions.includes(action);
}

/** Why an action is not on offer, in a sentence rather than as a greyed-out
 *  button with nothing to say for itself. Empty when it is on offer. */
export function whyNot(agent: Pick<AgentRow, "actions" | "front" | "broken" | "label">, action: string): string {
    if (may(agent, action)) return "";
    if (agent.broken) return "This row has no name, so nothing can be done to it.";
    if (agent.front && action === AGENT_EDIT) {
        return "The agent a pod answers with cannot be edited through the API — talk to it instead.";
    }
    if (agent.front && action === AGENT_REMOVE) {
        return "The agent a pod answers with cannot be deleted. It is the pod.";
    }
    if (action === AGENT_EDIT) return "You may read " + agent.label + ", not change it.";
    if (action === AGENT_REMOVE) return "You may not delete " + agent.label + ".";
    return "";
}

/* ── editing ───────────────────────────────────────────────────────
   The same arrangement as `session/profile-edit.ts`, and for the same reason:
   a PATCH that sends every field can write a stale value over a newer one, and
   a PATCH that omits an emptied field leaves it set forever. Unchanged is
   omitted; cleared is an explicit null. */

export interface AgentDraft {
    description: string;
    instruction: string;
}

export function draftOfAgent(detail: AgentDetail | null | undefined): AgentDraft {
    return {
        description: (detail?.description ?? "").trim(),
        /* Not trimmed. An instruction is a document — its blank lines and its
           trailing newline are how it was written, and normalising them makes
           every load look like an edit. */
        instruction: detail?.instruction ?? "",
    };
}

/** Only what changed. `description` is nullable on the API and `instruction`
 *  is not — `UpdateAgentRequest` gives it `min_length=1` — so clearing the
 *  first is a null and clearing the second is not an edit the API accepts. */
export function agentChanges(before: AgentDraft, after: AgentDraft): {
    description?: string | null;
    instruction?: string;
} {
    const patch: { description?: string | null; instruction?: string } = {};
    const description = after.description.trim();
    if (description !== before.description.trim()) {
        patch.description = description === "" ? null : description;
    }
    if (after.instruction !== before.instruction && after.instruction.trim() !== "") {
        patch.instruction = after.instruction;
    }
    return patch;
}

export function hasAgentChanges(before: AgentDraft, after: AgentDraft): boolean {
    return Object.keys(agentChanges(before, after)).length > 0;
}

/** What cannot be sent, said before the round trip.
 *
 *  Thin on purpose, like the profile form's: the server owns the rules, and a
 *  form inventing its own refuses things the API would have taken. Only the
 *  two the API states outright are checked here.
 */
export function agentProblems(draft: AgentDraft): Partial<Record<keyof AgentDraft, string>> {
    const found: Partial<Record<keyof AgentDraft, string>> = {};
    if (draft.instruction.trim() === "") {
        found.instruction = "An agent has to say what it is for. This cannot be emptied.";
    } else if (draft.instruction.length > MAX_INSTRUCTION) {
        found.instruction =
            "That is " + draft.instruction.length.toLocaleString() +
            " characters; the limit is " + MAX_INSTRUCTION.toLocaleString() + ".";
    }
    return found;
}

/** What a pinned runtime is called, when it is pinned at all. */
export function runtimeLine(detail: Pick<AgentDetail, "runtime">): string {
    if (!detail.runtime) return "";
    return detail.runtime.model
        ? detail.runtime.profile + " · " + detail.runtime.model
        : detail.runtime.profile;
}

/** The addresses that stop answering if this agent goes.
 *
 *  Deleting an agent tears its surfaces down — `teardown_agent_surfaces` runs
 *  before the row goes, because `agent_surfaces.agent_id` is ON DELETE SET
 *  NULL and an orphaned surface would start answering as the pod itself. So a
 *  confirmation can name them, which is the one consequence of this that
 *  reaches people outside the pod.
 *
 *  Matched on the *label*, not the row name. `Surface.agentName` has already
 *  been through `displayAgentName` by the time this app holds it — see
 *  `live.ts` — so a comparison against `invoice-filer` never matches the
 *  `Invoice filer` sitting in the field, and the confirmation would have
 *  promised that nothing breaks every single time.
 */
export function surfacesLost(
    surfaces: { agentName: string; platform: string; handle: string; active: boolean }[],
    agentLabel: string,
): { platform: string; handle: string }[] {
    return surfaces
        .filter((surface) => surface.active !== false && surface.agentName === agentLabel)
        .map((surface) => ({ platform: surface.platform, handle: surface.handle }));
}
