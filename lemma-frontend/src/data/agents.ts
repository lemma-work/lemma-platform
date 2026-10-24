import { displayAgentName, isPodDefaultAgent } from "./agent-names";
import { capabilities, firstSentence } from "@/stage/colleagues";
import { describeChoice, type Runtime } from "./runtimes";

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
    /** The uuid. A conversation names its agent by this alone (`agent_id`),
     *  so it is how a chat is matched back to the bot answering it. */
    id: string;
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

/** What an agent holds over a pod connector: running operations through it.
 *  Granted by the connector's slug, which is what the agent's tool names. */
export const CONNECTOR_USE = "connector.use";

function isConnectorGrant(grant: AgentGrant): boolean {
    return grant.resource.toLowerCase() === "connector";
}

/** The pod connectors an agent may use, by slug. */
export function connectorGrantSlugs(grants: AgentGrant[]): string[] {
    return grants.filter(isConnectorGrant).map((grant) => grant.name);
}

/** The agent's grants with its connector grants replaced by `slugs`: every
 *  other grant goes back exactly as it was, because the server replaces the
 *  whole set and a grant left out is a grant taken away. */
export function withConnectorGrants(grants: AgentGrant[], slugs: string[]): AgentGrant[] {
    const kept = grants.filter((grant) => !isConnectorGrant(grant));
    const unique = [...new Set(slugs.map((slug) => slug.trim()).filter(Boolean))];
    return [...kept, ...unique.map((slug) => ({ resource: "connector", name: slug, permissions: [CONNECTOR_USE] }))];
}

/** The body of `PUT /pods/{pod}/agents/{name}/permissions`. Resource types
 *  go as the API spells them, lowercase. */
export function grantsBody(grants: AgentGrant[]): { grants: { resource_type: string; resource_name: string; permission_ids: string[] }[] } {
    return {
        grants: grants.map((grant) => ({
            resource_type: grant.resource.toLowerCase(),
            resource_name: grant.name,
            permission_ids: grant.permissions,
        })),
    };
}

export interface AgentDetail extends AgentRow {
    description: string;
    instruction: string;
    /** `agent_runtime`, when one is pinned. `model` is empty when the profile
     *  is named but the model is left to the profile's own default — the API
     *  drops `model_name` from the payload entirely in that case, so an empty
     *  string here and a missing key there are the same fact. */
    runtime: PinnedRuntime | null;
    /** The machine its commands run on, as a ref (`pod:<name>`). Null runs
     *  them on the default machine of whoever the run acts for. */
    machine: string | null;
    input: SchemaNote | null;
    output: SchemaNote | null;
    grants: AgentGrant[];
}

/** `agent_runtime` as stored. `settings` is kept whole -- every generation
 *  field, not only the two this page edits -- because a save writes the
 *  runtime back, and a field dropped here would be erased there. */
export interface PinnedRuntime {
    profile: string;
    model: string;
    settings: Record<string, unknown>;
    /** A coding agent's option values for this agent alone — effort, mode —
     *  keyed as the agent published them. Empty is "as the runtime has them". */
    selections: Record<string, string>;
}

/** `GenerationSettings.reasoning_effort`, as the API spells it. */
export const REASONING_EFFORTS = ["none", "minimal", "low", "medium", "high"] as const;

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** The string-valued entries of an object: a selection is one of the values
 *  an agent published, and those are strings. */
function stringValues(value: unknown): Record<string, string> {
    const entries = Object.entries(object(value) ?? {});
    return Object.fromEntries(entries.filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== ""));
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
        id: text(item.id),
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

/** The space's own agent, under the teammate's name.
 *
 *  The pod is the teammate: it answers under its own name and wears its own
 *  face, and the agent row behind it is plumbing. `displayAgentName` is handed
 *  a wire name and no pod, so for that row it can only say "Lem" — which is
 *  how one space came to call its teammate three things at once: its name on
 *  Home, "Lem" in the agent list, and "main bot" in the copy between them.
 *  So every list of agents passes through here once the pod's name is known.
 */
export function answeringAs<T extends { front: boolean; label: string }>(rows: T[], teammate: string | null | undefined): T[] {
    const name = teammate?.trim();
    if (!name) return rows;
    return rows.map((row) => (row.front && row.label !== name ? { ...row, label: name } : row));
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
        runtime: profile
            ? {
                profile,
                model: text(runtime?.model_name),
                settings: object(runtime?.settings) ?? {},
                selections: stringValues(runtime?.config_selections),
            }
            : null,
        machine: text(item.machine) || null,
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
    /** Empty is "not set here": the model's or the provider's own default. */
    reasoningEffort: string;
    /** Digits as typed; empty is unset. */
    maxOutputTokens: string;
    /** A coding agent's option values; see `PinnedRuntime.selections`. */
    selections: Record<string, string>;
    /** Carried, not edited: the settings live on it, so saving them writes it. */
    runtime: PinnedRuntime | null;
    /** A machine ref; empty is the default machine of whoever the run acts for. */
    machine: string;
}

export function draftOfAgent(detail: AgentDetail | null | undefined): AgentDraft {
    const settings = detail?.runtime?.settings ?? {};
    const effort = settings.reasoning_effort;
    const cap = settings.max_output_tokens;
    return {
        description: (detail?.description ?? "").trim(),
        /* Not trimmed. An instruction is a document — its blank lines and its
           trailing newline are how it was written, and normalising them makes
           every load look like an edit. */
        instruction: detail?.instruction ?? "",
        reasoningEffort: typeof effort === "string" ? effort : "",
        maxOutputTokens: typeof cap === "number" ? String(cap) : "",
        selections: { ...(detail?.runtime?.selections ?? {}) },
        runtime: detail?.runtime ?? null,
        machine: detail?.machine ?? "",
    };
}

export interface AgentRuntimePatch {
    profile_id: string;
    model_name?: string;
    settings: Record<string, unknown> | null;
    config_selections?: Record<string, string>;
}

/** The pinned runtime with the edited settings written into it.
 *
 *  Whole, because `agent_runtime` is replaced rather than merged on PATCH:
 *  the profile, the model, every other setting and the agent's selections go
 *  back as they were — leaving the selections out would clear them. */
function runtimeWithSettings(runtime: PinnedRuntime, draft: AgentDraft): AgentRuntimePatch {
    const settings: Record<string, unknown> = { ...runtime.settings };
    delete settings.reasoning_effort;
    delete settings.max_output_tokens;
    if (draft.reasoningEffort) settings.reasoning_effort = draft.reasoningEffort;
    if (draft.maxOutputTokens.trim()) settings.max_output_tokens = Number(draft.maxOutputTokens.trim());
    const selections = Object.fromEntries(Object.entries(draft.selections).filter(([, value]) => Boolean(value)));
    return {
        profile_id: runtime.profile,
        ...(runtime.model ? { model_name: runtime.model } : {}),
        settings: Object.keys(settings).length ? settings : null,
        ...(Object.keys(selections).length ? { config_selections: selections } : {}),
    };
}

function sameSelections(a: Record<string, string>, b: Record<string, string>): boolean {
    const live = (selections: Record<string, string>) => Object.entries(selections).filter(([, value]) => Boolean(value));
    const left = live(a);
    return left.length === live(b).length && left.every(([key, value]) => b[key] === value);
}

export interface AgentPatch {
    description?: string | null;
    instruction?: string;
    agent_runtime?: AgentRuntimePatch;
    machine?: string | null;
}

/** Only what changed. `description` is nullable on the API and `instruction`
 *  is not — `UpdateAgentRequest` gives it `min_length=1` — so clearing the
 *  first is a null and clearing the second is not an edit the API accepts. */
export function agentChanges(before: AgentDraft, after: AgentDraft): AgentPatch {
    const patch: AgentPatch = {};
    const description = after.description.trim();
    if (description !== before.description.trim()) {
        patch.description = description === "" ? null : description;
    }
    if (after.instruction !== before.instruction && after.instruction.trim() !== "") {
        patch.instruction = after.instruction;
    }
    const generation =
        after.reasoningEffort !== before.reasoningEffort ||
        after.maxOutputTokens.trim() !== before.maxOutputTokens.trim() ||
        !sameSelections(before.selections, after.selections);
    if (generation && after.runtime) {
        patch.agent_runtime = runtimeWithSettings(after.runtime, after);
    }
    /* Back to the default is a null, not an omission: omitted leaves the
       machine it had. */
    if (after.machine !== before.machine) {
        patch.machine = after.machine || null;
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
    const cap = draft.maxOutputTokens.trim();
    if (cap && !/^[1-9][0-9]*$/.test(cap)) {
        found.maxOutputTokens = "A whole number of tokens, or empty for the model's own limit.";
    }
    if (draft.instruction.trim() === "") {
        found.instruction = "An agent has to say what it is for. This cannot be emptied.";
    } else if (draft.instruction.length > MAX_INSTRUCTION) {
        found.instruction =
            "That is " + draft.instruction.length.toLocaleString() +
            " characters; the limit is " + MAX_INSTRUCTION.toLocaleString() + ".";
    }
    return found;
}

/** What a pinned runtime is called, when it is pinned at all.
 *
 *  `agent_runtime` holds the profile's id, which is nothing a person named.
 *  Given the organization's runtimes — the whole list, retired ones included —
 *  it says the profile's name the way the model picker does, and says when the
 *  profile has been retired: an agent pinned to one cannot take a run, and
 *  that is the one thing about it worth knowing. The id is the fallback while
 *  the list loads, or for a profile this person cannot see. */
export function runtimeLine(detail: Pick<AgentDetail, "runtime">, runtimes?: readonly Runtime[]): string {
    if (!detail.runtime) return "";
    const { profile, model } = detail.runtime;
    const runtime = runtimes?.find((entry) => entry.id === profile);
    const said = runtime ? describeChoice([runtime], { runtimeId: profile, model }) : "";
    if (!runtime || !said) return model ? profile + " · " + model : profile;
    return runtime.archived ? runtime.name + " (retired)" + said.slice(runtime.name.length) : said;
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
