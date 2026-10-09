/** What a workflow *is*, as opposed to what it did.
 *
 *  The list response deliberately omits the graph and hands back a
 *  `node_count` instead (`api/schemas.py:444`), so until somebody fetches one
 *  workflow the only thing this app could say about "what it runs the same way
 *  twice" was a number. `workflows.get(name)` carries `nodes`, `edges` and
 *  `start` (`api/schemas.py:414`); this turns those three into a start line,
 *  a list of steps in the order they run, and an outline of the same steps
 *  that forks where the workflow does.
 *
 *  Two things about the payload set the whole shape of this file.
 *
 *  `start` is the *trigger* — MANUAL, a schedule, a connector event, a table
 *  change (`domain/start.py:14`). It is not a pointer at the first step. The
 *  first step is `entry_node_id`, which the backend computes and stores at
 *  save time (`domain/workflow.py:40`) and then does not put on
 *  `WorkflowResponse`. So the entry has to be derived here, by the same rule
 *  the validator uses.
 *
 *  And the graph is not all in `edges`. A decision's branches are
 *  `config.rules[].next_node_id` and a loop's body is `config.child_node_id`
 *  (`domain/graph.py:106`); neither is an edge. A walk that follows only
 *  `edges` silently loses every branch arm and every loop body and then
 *  reports them as unreachable, which is the most confident kind of wrong.
 *
 *  Nothing here throws and nothing here drops a node. A step that arrived as
 *  nonsense is still a step that exists in somebody's workflow, and a shape
 *  that is one step short is worse than one with an ugly row in it.
 */

import { isRecord, sayAnswer, sayFor, str } from "./runs";

/* ── the kinds, as the backend spells them ─────────────────────────── */

/** `domain/nodes/base.py:11`. */
export const NODE_KINDS = ["FORM", "AGENT", "FUNCTION", "DECISION", "LOOP", "WAIT_UNTIL", "END"] as const;
export type NodeKind = (typeof NODE_KINDS)[number];

/** `domain/start.py:16`. */
export const START_KINDS = ["MANUAL", "SCHEDULED", "EVENT", "DATASTORE_EVENT"] as const;
export type StartKind = (typeof START_KINDS)[number];

/* ── how it starts ─────────────────────────────────────────────────── */

export interface StartLine {
    /** As sent, or "" when the payload said nothing this app recognises. */
    kind: string;
    /** Which of the three things a start can be, in a person's words. */
    says: string;
    /** Whatever else the config carried, one fact per line. */
    detail: string[];
}

/** The trigger, said out loud.
 *
 *  Three sentences for four types, because DATASTORE_EVENT and EVENT are the
 *  same answer to the question a person is asking — something happened, and
 *  this went. Which something is the detail line's job.
 */
export function readStart(raw: unknown): StartLine {
    if (!isRecord(raw)) {
        /* `start` is `WorkflowStartOutput | null` (`api/schemas.py:414`), and
           a null one is not a manual one — it is a workflow whose trigger was
           never set. Guessing "runs when you ask" for it would put a run
           button's worth of confidence behind nothing. */
        return { kind: "", says: "How this starts is not recorded.", detail: [] };
    }
    const kind = str(raw.type) ?? "";
    const config = isRecord(raw.config) ? raw.config : null;

    if (kind === "MANUAL") return { kind, says: "Runs when you ask", detail: [] };

    if (kind === "SCHEDULED") {
        return { kind, says: "Runs on a schedule", detail: saySchedule(config) };
    }

    if (kind === "EVENT") {
        return { kind, says: "Runs when something happens", detail: sayConnector(config) };
    }

    if (kind === "DATASTORE_EVENT") {
        return { kind, says: "Runs when something happens", detail: sayTableChange(config) };
    }

    return {
        kind,
        says: kind ? "Starts in a way this app does not know (" + kind + ")" : "How this starts is not recorded.",
        detail: [],
    };
}

/** The one thing a scheduled start does *not* carry is the time.
 *
 *  `ScheduledWorkflowStartConfig` is a single `schedule_type` field, and its
 *  own description says the concrete values come from pod schedules
 *  (`domain/start.py:47`). So this says where to go looking rather than
 *  leaving a person to assume the workflow is the thing to edit.
 */
function saySchedule(config: Record<string, unknown> | null): string[] {
    const type = str(config?.schedule_type);
    const when =
        type === "ONCE" ? "Once." :
        type === "CRON" ? "Over and over." :
        "";
    const where = "The timetable lives on a pod schedule, not on the workflow.";
    return [when ? when + " " + where : where];
}

function sayConnector(config: Record<string, unknown> | null): string[] {
    const connector = str(config?.connector_id);
    const trigger = str(config?.connector_trigger_id);
    const lines: string[] = [];
    if (connector && trigger) lines.push("Sent by " + connector + ", on " + trigger + ".");
    else if (trigger) lines.push("On " + trigger + ".");
    else if (connector) lines.push("Sent by " + connector + ".");
    else lines.push("The connector that triggers it was not in the payload.");

    /* `trigger_config` is a free-form dict per connector, so its keys are the
       only part that means anything without knowing the connector. Naming them
       says "this is filtered" without pretending to explain the filter. */
    const settings = isRecord(config?.trigger_config) ? Object.keys(config.trigger_config) : [];
    if (settings.length) lines.push("Narrowed by " + settings.slice(0, 6).join(", ") + ".");
    return lines;
}

function sayTableChange(config: Record<string, unknown> | null): string[] {
    const table = str(config?.table_name);
    const ops = Array.isArray(config?.operations)
        ? config.operations.filter((one): one is string => typeof one === "string")
        : [];
    const did = sayOperations(ops);
    if (!table) return ["A table this payload did not name " + did + "."];
    return ["When a row in " + table + " is " + did + "."];
}

/** INSERT / UPDATE / DELETE as things that happen to a row. */
function sayOperations(ops: string[]): string {
    const said = ops
        .map((one) => (one === "INSERT" ? "added" : one === "UPDATE" ? "changed" : one === "DELETE" ? "removed" : one.toLowerCase()))
        .filter(Boolean);
    if (said.length === 0) return "touched at all";
    if (said.length === 1) return said[0];
    return said.slice(0, -1).join(", ") + " or " + said[said.length - 1];
}

/* ── one step ──────────────────────────────────────────────────────── */

export interface FlowStep {
    /** The node id. Not decoration: it is what a failed run names in
     *  `failed_node_id` and what every step row in the history is keyed on,
     *  so the two views can be read against each other. */
    id: string;
    /** FORM / AGENT / … as sent, or "" when the payload did not say. */
    kind: string;
    /** The author's own name for it, when they wrote one (`BaseNode.label`). */
    label: string | null;
    /** What this step does, in a person's words. */
    says: string;
    /** The rest of what its config carries, one fact per line. */
    detail: string[];
    /** Where it can go that is not an edge: a decision's rule targets, a
     *  loop's body. Kept because the walk needs them and the row does not. */
    branches: string[];
    /** A decision's rules, in the order they are tried. Empty on every other
     *  kind. The outline draws one arm per rule, so the rows say nothing
     *  about them. */
    rules: FlowRule[];
    /** The payload was not a readable node at all. Drawn anyway. */
    unreadable: boolean;
}

export interface FlowRule {
    /** The condition as written, or a sentence when the payload had none. */
    when: string;
    /** The step it goes to, or "" when it named none. */
    to: string;
}

/** One node, guarded down to the last field.
 *
 *  Never returns null, unlike every reader in `runs.ts`. A run without an id
 *  is a row that cannot be opened, so dropping it costs nothing; a *step*
 *  without an id is still a step this workflow runs, and a graph drawn one
 *  step short is a graph that lies about what a teammate does.
 */
export function readFlowStep(raw: unknown, at: number): FlowStep {
    if (!isRecord(raw)) {
        return {
            id: "",
            kind: "",
            label: null,
            says: "Step " + (at + 1) + " came back in a shape this app could not read.",
            detail: [],
            branches: [],
            rules: [],
            unreadable: true,
        };
    }
    const id = str(raw.id) ?? "";
    const kind = str(raw.type) ?? "";
    const label = str(raw.label);
    const config = isRecord(raw.config) ? raw.config : null;
    const base = { id, kind, label, rules: [], unreadable: id === "" };

    switch (kind) {
        case "FORM": return { ...base, says: "Asks a person", ...sayForm(config) };
        case "AGENT": return { ...base, says: sayTarget("Hands it to", config?.agent_name, "an agent"), ...sayInputs(config) };
        case "FUNCTION": return { ...base, says: sayTarget("Runs", config?.function_name, "a function"), ...sayInputs(config) };
        case "DECISION": return { ...base, ...sayDecision(config) };
        case "LOOP": return { ...base, says: "Repeats for each item", ...sayLoop(config) };
        case "WAIT_UNTIL": return { ...base, says: "Waits", detail: sayTimeout(config), branches: [] };
        case "END": return { ...base, says: "Ends the run", detail: [], branches: [] };
        default:
            return {
                ...base,
                says: kind ? "A kind of step this app does not know (" + kind + ")" : "A step that did not say what kind it is",
                detail: [],
                branches: [],
            };
    }
}

function sayTarget(verb: string, name: unknown, fallback: string): string {
    const said = str(name);
    return said ? verb + " " + said : verb + " " + fallback + " the payload did not name";
}

/** A form node's schema is the *template*, not the form.
 *
 *  `input_schema` here can hold typed input bindings —
 *  `{"type": "expression", "value": "…"}` in place of a value — that the form
 *  executor resolves against the run context at suspend time
 *  (`domain/graph.py:120`). So this reads titles and keys off it and stops
 *  there: handing it to the SDK's form builder, the way `form.tsx` does with a
 *  wait's *resolved* schema, would render a control for something that is not
 *  a value yet.
 */
function sayForm(config: Record<string, unknown> | null): { detail: string[]; branches: string[] } {
    const detail: string[] = [];
    const schema = isRecord(config?.input_schema) ? config.input_schema : null;
    const properties = isRecord(schema?.properties) ? schema.properties : null;
    const required = new Set(
        Array.isArray(schema?.required) ? schema.required.filter((one): one is string => typeof one === "string") : [],
    );

    const names = properties ? Object.keys(properties) : [];
    if (names.length) {
        const said = names.slice(0, 8).map((key) => {
            const field = properties && isRecord(properties[key]) ? properties[key] : null;
            const title = str(field?.title) ?? key;
            return required.has(key) ? title + "*" : title;
        });
        detail.push("For: " + said.join(", ") + (names.length > 8 ? ", and " + (names.length - 8) + " more" : ""));
    } else {
        /* A form node with no fields is legal and means something: an
           acknowledgement, where the answer is that somebody pressed it. */
        detail.push("Nothing to fill in — it waits for somebody to say it is done.");
    }

    const expression = str(config?.assignee_pod_member_id_expression);
    if (expression) detail.push("Assigned by " + expression);
    else if (str(config?.assignee_pod_member_id)) detail.push("Assigned to one person here.");

    return { detail, branches: [] };
}

/** What an agent or a function is handed.
 *
 *  Keys only. The values are `InputBinding`s — an expression or a literal
 *  (`domain/nodes/bindings.py:9`) — and an expression printed beside its key
 *  doubles the line length for something only the workflow's author reads.
 *  Which inputs exist is the part that answers "what does this step need".
 */
function sayInputs(config: Record<string, unknown> | null): { detail: string[]; branches: string[] } {
    const mapping = isRecord(config?.input_mapping) ? Object.keys(config.input_mapping) : [];
    if (mapping.length === 0) return { detail: [], branches: [] };
    return {
        detail: ["With: " + mapping.slice(0, 8).join(", ") + (mapping.length > 8 ? ", and " + (mapping.length - 8) + " more" : "")],
        branches: [],
    };
}

/** The branches, with their conditions.
 *
 *  The rules come back as data rather than as detail lines, because the
 *  outline draws each one as an arm with its steps under it, and a condition
 *  printed both on the row and over its arm is the same fact twice. The first
 *  truthy rule wins and the outgoing edge is the fall-through
 *  (`domain/nodes/decision.py:26`), so the order is kept exactly.
 */
function sayDecision(config: Record<string, unknown> | null): { says: string; detail: string[]; branches: string[]; rules: FlowRule[] } {
    if (isRecord(config?.question)) return sayQuestion(config.question);
    const raw = Array.isArray(config?.rules) ? config.rules : [];
    const detail: string[] = [];
    const branches: string[] = [];
    const rules: FlowRule[] = [];
    for (const rule of raw) {
        if (!isRecord(rule)) {
            detail.push("One of its branches came back unreadable.");
            continue;
        }
        const target = str(rule.next_node_id);
        if (target) branches.push(target);
        rules.push({ when: str(rule.condition) ?? "a condition the payload did not carry", to: target ?? "" });
    }
    if (raw.length === 0) detail.push("No branches — it falls straight through.");
    return { says: "Branches", detail, branches, rules };
}

/** A decision that asks one closed question about some evidence and branches
 *  on the answer (`DecisionQuestion`, `domain/nodes/decision.py`).
 *
 *  The question is the `description` of its `answer` schema, which is what a
 *  person would ask in the step's place, so it is what the row says. Each
 *  answer with a route, then the unsure route, comes back as a rule -- the
 *  same order the run tries them in -- so the outline draws them as arms like
 *  any decision's, with the fall-through as its "otherwise". What no arm can
 *  show is the one detail line: a question that cannot be answered at all
 *  stops the run rather than taking a branch.
 */
function sayQuestion(question: Record<string, unknown>): { says: string; detail: string[]; branches: string[]; rules: FlowRule[] } {
    const answer = isRecord(question.answer) ? question.answer : null;
    const asked = str(answer?.description);
    const branches: string[] = [];
    const rules: FlowRule[] = [];
    const routes = isRecord(question.routes) ? question.routes : {};
    for (const [key, target] of Object.entries(routes)) {
        const next = str(target);
        if (next) branches.push(next);
        rules.push({ when: sayAnswer(key), to: next ?? "" });
    }
    const unsure = str(question.unsure_next_node_id);
    if (unsure) {
        branches.push(unsure);
        rules.push({ when: "it cannot tell", to: unsure });
    }
    return {
        says: asked ? "Asks: " + asked : "Branches on a judgement",
        detail: ["If it cannot be answered at all, the run stops."],
        branches,
        rules,
    };
}

/** The body is not a detail line either: the outline draws it inside the
 *  loop. Only a body that points at nothing is said, since there is then
 *  nothing to draw. */
function sayLoop(config: Record<string, unknown> | null): { detail: string[]; branches: string[] } {
    const detail: string[] = [];
    const over = str(config?.items_path);
    const alias = str(config?.item_var_name) ?? "item";
    detail.push(over ? "Over " + over + ", as loop." + alias : "Over a list the payload did not name");
    const body = str(config?.child_node_id);
    if (!body) detail.push("Its body points at nothing.");
    return { detail, branches: body ? [body] : [] };
}

function sayTimeout(config: Record<string, unknown> | null): string[] {
    const seconds = config?.timeout_seconds;
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
        return ["For a length of time the payload did not carry."];
    }
    return ["Up to " + (sayFor(seconds * 1000) ?? "no time at all") + "."];
}

/* ── the order they run in ─────────────────────────────────────────── */

interface EdgeRow { source: string; target: string }

function readEdges(raw: unknown): EdgeRow[] {
    const list = Array.isArray(raw) ? raw : [];
    const out: EdgeRow[] = [];
    for (const edge of list) {
        if (!isRecord(edge)) continue;
        const source = str(edge.source);
        const target = str(edge.target);
        if (source && target) out.push({ source, target });
    }
    return out;
}

export interface Ordering {
    /** Every step the first one reaches, in the order it reaches them. */
    ordered: FlowStep[];
    /** Everything else, walked the same way so an orphaned chain still reads
     *  in order. An orphan is a real thing to find out about a workflow —
     *  usually a step somebody rewired and left behind. */
    orphans: FlowStep[];
    /** Said above the list when the graph could not be walked the ordinary
     *  way. Null when it could. */
    trouble: string | null;
}

/** Steps in run order.
 *
 *  Three passes, and each is doing a job the other two cannot.
 *
 *  **Find the first step.** `entry_node_id` is the backend's own answer and is
 *  preferred whenever it names a real node, exactly as the engine does before
 *  every run (`execution/engine.py:485`) — except that `WorkflowResponse` does
 *  not carry it, so in practice this always falls through to deriving it the
 *  way the validator does: the node nothing points at, counting edges,
 *  decision rules and loop bodies as pointing (`domain/graph.py:150`).
 *
 *  **Walk it depth-first** to learn which steps the first one reaches at all,
 *  and in what order somebody following one path would meet them. A `seen` set
 *  is the cycle guard: a loop's body ends in an edge back to the loop, so a
 *  walk without one never returns.
 *
 *  **Then sort topologically**, so a step that can only happen after another
 *  is printed after it. Depth-first order alone puts a join step in the middle
 *  of the first branch arm and the second arm underneath it, which reads as
 *  "and then, unrelatedly". Kahn's algorithm with the depth-first order as the
 *  tie-break keeps a straight chain straight and puts two branch arms side by
 *  side with their join beneath both.
 *
 *  A cycle has no topological order at all, so when the queue empties with
 *  steps still in hand the earliest one found is taken and the sort carries on
 *  — which for the only cycle the language can express, a loop body edged back
 *  to its loop, prints the loop, then its body, then what follows.
 */
export function orderSteps(steps: FlowStep[], edges: EdgeRow[], entryId: string | null): Ordering {
    if (steps.length === 0) return { ordered: [], orphans: [], trouble: null };

    /* By position, not by id: a duplicate id is a graph the validator would
       have refused (`domain/graph.py:63`), but this reads what arrived rather
       than what should have. The first wins the name and the second falls out
       as an orphan, which is visible, rather than overwriting it, which is
       not. */
    const where = new Map<string, number>();
    steps.forEach((step, at) => {
        if (step.id && !where.has(step.id)) where.set(step.id, at);
    });

    const out = steps.map((step) => {
        /* Branches before edges, and the order is the semantics: a decision
           tries its rules before falling through to its edge, and a loop runs
           its body before it moves on. Reversing them prints what comes after
           a loop above what happens inside it.

           A target no node answers to is dropped here rather than reported:
           the validator refuses those on save (`domain/graph.py:74`), so one
           on the wire means a graph written round the API — and the row that
           owns it already names where it meant to go. */
        const targets = [...step.branches, ...edges.filter((one) => one.source === step.id).map((one) => one.target)];
        const next: number[] = [];
        for (const target of targets) {
            const to = where.get(target);
            if (to !== undefined && !next.includes(to)) next.push(to);
        }
        return next;
    });

    const pointedAt = new Set<number>();
    for (const next of out) for (const to of next) pointedAt.add(to);

    /* Steps nothing points at. One of them is the first step; more than one is
       a graph the engine will not start (`domain/graph.py:150`), and none at
       all is a ring with no way in. */
    const candidates = steps.map((_, at) => at).filter((at) => steps[at].id && !pointedAt.has(at));

    let trouble: string | null = null;
    const named = entryId ? where.get(entryId) : undefined;
    if (entryId && named === undefined) {
        trouble =
            "This workflow names " + entryId + " as its first step, and there is no step by that name. " +
            "The order below was worked out from the wiring instead.";
    }

    let entry: number;
    if (named !== undefined) {
        entry = named;
    } else if (candidates.length === 1) {
        entry = candidates[0];
    } else if (candidates.length > 1) {
        /* Not walked as several starts. A workflow has one trigger and a run
           begins at one node, so several steps with nothing pointing at them
           is an ambiguity this app cannot resolve and the engine refuses to.
           The first the payload lists heads the order; the rest fall where
           they belong, under the steps nothing reaches. */
        entry = candidates[0];
        trouble = (trouble ? trouble + " " : "") +
            "Nothing points at " + candidates.length + " of these steps — " +
            candidates.map((at) => steps[at].id).join(", ") +
            " — so there is no single first one, and a run cannot start until that is settled.";
    } else {
        entry = steps.findIndex((step) => Boolean(step.id));
        trouble = (trouble ? trouble + " " : "") +
            "Every step here is pointed at by another, so there is no first one. " +
            "The order below starts where the payload does.";
    }

    const all = new Set(steps.map((_, at) => at));
    const first = entry >= 0 ? sequence(out, [entry], all) : [];

    /* Whatever the first step never reaches, walked the same way. An orphan
       chain of four is four steps in the order they would run if anything
       ever started them, not four rows in whatever order the payload held. */
    const left = new Set(steps.map((_, at) => at).filter((at) => !first.includes(at)));
    const heads = [...left].filter((at) => steps[at].id && !out.some((next, from) => left.has(from) && next.includes(at)));
    const rest = sequence(out, heads, left, true);

    return {
        ordered: first.map((at) => steps[at]),
        orphans: rest.map((at) => steps[at]),
        trouble,
    };
}

/** Depth-first for discovery, Kahn for the printed order.
 *
 *  `topUp` is what separates the two callers. The run order starts at exactly
 *  one step and stops where that step's reach stops — anything else would be
 *  claiming a run goes somewhere it does not. The leftovers have no single
 *  head and the promise there is different: every step that came in comes out,
 *  so it keeps taking the earliest one still unplaced until none are left.
 */
function sequence(out: number[][], roots: number[], allowed: Set<number>, topUp = false): number[] {
    const rank = new Map<number, number>();
    const found: number[] = [];

    /* Iterative rather than recursive: the depth is the longest chain, which
       is small, but a stack is the same code and cannot be the thing that
       takes the panel down on a graph somebody generated. */
    const discover = (from: number) => {
        const stack = [from];
        while (stack.length) {
            const at = stack.pop();
            if (at === undefined || rank.has(at) || !allowed.has(at)) continue;
            rank.set(at, found.length);
            found.push(at);
            /* Reversed, because a stack hands back the last thing pushed and
               the first branch should be the first one walked. */
            for (const to of [...out[at]].reverse()) if (!rank.has(to)) stack.push(to);
        }
    };

    for (const root of roots) discover(root);
    if (topUp) for (const at of [...allowed].sort((left, right) => left - right)) discover(at);

    const into = new Map<number, number>();
    for (const at of found) into.set(at, 0);
    for (const at of found) for (const to of out[at]) if (into.has(to)) into.set(to, (into.get(to) ?? 0) + 1);

    const placed: number[] = [];
    const done = new Set<number>();
    while (done.size < found.length) {
        /* The earliest-found among whatever is ready — and, when nothing is
           ready, among whatever is left, which is how a cycle gets broken. A
           linear scan, because a workflow with enough steps for that to cost
           anything is one nobody can read. */
        const pickFrom = (ready: boolean) => {
            let pick = -1;
            for (const at of found) {
                if (done.has(at)) continue;
                if (ready && (into.get(at) ?? 0) !== 0) continue;
                if (pick === -1 || (rank.get(at) ?? 0) < (rank.get(pick) ?? 0)) pick = at;
            }
            return pick;
        };
        const pick = pickFrom(true) === -1 ? pickFrom(false) : pickFrom(true);
        if (pick === -1) break;
        done.add(pick);
        placed.push(pick);
        for (const to of out[pick]) {
            if (!done.has(to) && into.has(to)) into.set(to, Math.max(0, (into.get(to) ?? 0) - 1));
        }
    }
    return placed;
}

/* ── the way it branches ───────────────────────────────────────────── */

/** One row of the drawn outline. */
export type FlowItem =
    | { type: "step"; step: FlowStep }
    /** A decision with an arm per rule and one for when none match. `then`
     *  is the step drawn straight after the fork — where the arms meet again,
     *  or the arm kept on the main line — or null when every arm is drawn in
     *  full. `on` is what every rule compares, when they all compare the
     *  same thing, and then each arm's `when` is only the value. */
    | { type: "fork"; step: FlowStep; arms: FlowArm[]; then: FlowStep | null; on: string | null }
    /** A loop with its body drawn inside it. */
    | { type: "loop"; step: FlowStep; body: FlowItem[] }
    /** Goes on to a step drawn somewhere else. `back` when that step comes
     *  earlier on the same path, which is a retry. `to` is null when the
     *  step it names does not exist. */
    | { type: "jump"; to: FlowStep | null; id: string; back: boolean }
    /** A path that stops where the drawing would otherwise suggest it carries
     *  on: the end of the run, or of one pass of a loop. */
    | { type: "end"; inLoop: boolean };

export interface FlowArm {
    /** The rule's condition, or null for the arm taken when none match. */
    when: string | null;
    /** Empty when the arm goes straight to the fork's `then`. */
    items: FlowItem[];
}

/** No step: the end of the run, or of one pass of a loop. */
const EXIT = -1;

interface Wiring {
    steps: FlowStep[];
    /** The engine's next step: the first edge out, which is the only one it
     *  ever reads (`domain/workflow.py:71`). */
    next: (number | undefined)[];
    /** A decision's rules with their targets found. `to` is undefined when
     *  the rule names a step that does not exist. */
    rules: { when: string; to: number | undefined; id: string }[][];
    body: (number | undefined)[];
    /** The step drawn after a decision's fork: where its arms meet again, or
     *  the arm kept on the main line when they never do. */
    join: (number | null)[];
    /** The join is a kept arm, not a meeting point. */
    kept: boolean[];
    placed: Set<number>;
}

/** Every step, laid out the way a person would explain it.
 *
 *  A topological list puts a decision's arms one after another, so "post",
 *  "wait an hour" and "wait a day" read as three things that all happen. Here
 *  a decision forks: each rule is an arm with its own steps under it, and the
 *  arms close again at the first step every one of them reaches. That step is
 *  the decision's immediate postdominator, and it goes back on the main line
 *  under the fork, so a branch that rejoins reads as a detour rather than as
 *  a second copy of the rest of the workflow.
 *
 *  Two rules make the postdominators match what people mean.
 *
 *  A decision with no outgoing edge ends the run when no rule matches. That
 *  way out is drawn as its own arm saying the run ends, so it is left out of
 *  where the arms meet. Counting it would mean no decision without an edge
 *  ever rejoins, and a guard clause — wait and check again, or stop — would
 *  push the whole rest of the workflow into its first arm.
 *
 *  Inside a loop's body, an edge back to the loop and the end of a chain both
 *  mean "next item" (`execution/stepper.py:191`). Both count as leaving.
 *
 *  Arms that never meet again — post, or wait and ask again, or stop, each
 *  ending its own way — have no such step. Nesting every arm in full there
 *  buries the workflow's real work inside its first arm and pushes the other
 *  arms a screen away from the question they answer. So the arm with the most
 *  steps stays on the main line, drawn after the fork as if it were where
 *  the arms meet, and the others read as side trips that visibly end or go
 *  back. An arm that leads back to the decision never stays: that is the
 *  retry, and the side trip is what it is.
 *
 *  A step reached a second way is drawn once, where the walk meets it first,
 *  and every other path to it says where it goes: back, when the step is
 *  earlier on the same path, otherwise on.
 */
export function outline(steps: FlowStep[], edges: EdgeRow[], entry: number): { flow: FlowItem[]; loose: FlowItem[][] } {
    const wiring = wire(steps, edges);
    const flow = entry >= 0 ? walk(wiring, entry, [], null, []) : [];

    /* What the first step never reaches, from each head in turn. A ring with
       no head still gets drawn: the first undrawn step starts it. */
    const loose: FlowItem[][] = [];
    for (;;) {
        const left = steps.map((_, at) => at).filter((at) => !wiring.placed.has(at));
        if (left.length === 0) break;
        const pointed = new Set(left.flatMap((at) => targetsOf(wiring, at)));
        const head = left.find((at) => !pointed.has(at)) ?? left[0];
        loose.push(walk(wiring, head, [], null, []));
    }
    return { flow, loose };
}

function wire(steps: FlowStep[], edges: EdgeRow[]): Wiring {
    const where = new Map<string, number>();
    steps.forEach((step, at) => {
        if (step.id && !where.has(step.id)) where.set(step.id, at);
    });
    const find = (id: string) => (id ? where.get(id) : undefined);

    const next = steps.map((step) => {
        if (!step.id || step.kind === "END") return undefined;
        const edge = edges.find((one) => one.source === step.id);
        return edge ? find(edge.target) : undefined;
    });
    const rules = steps.map((step) =>
        step.kind === "DECISION" ? step.rules.map((rule) => ({ when: rule.when, to: find(rule.to), id: rule.to })) : [],
    );
    const body = steps.map((step) => (step.kind === "LOOP" ? find(step.branches[0] ?? "") : undefined));

    const wiring: Wiring = { steps, next, rules, body, join: [], kept: [], placed: new Set() };
    Object.assign(wiring, joinsOf(wiring));
    return wiring;
}

function targetsOf(wiring: Wiring, at: number): number[] {
    const out: number[] = [];
    for (const rule of wiring.rules[at]) if (rule.to !== undefined) out.push(rule.to);
    if (wiring.body[at] !== undefined) out.push(wiring.body[at]);
    if (wiring.next[at] !== undefined) out.push(wiring.next[at]);
    return out;
}

/** Where each decision's arms meet again; failing that, the arm to keep on
 *  the main line; failing that, null.
 *
 *  Postdominator sets by plain iteration rather than Cooper–Harvey–Kennedy:
 *  a workflow anybody can read has tens of steps, and this is the version
 *  whose correctness can be seen. Each set starts full and only shrinks, so
 *  it settles on the largest answer — which is what lets a retry loop that
 *  comes back round still count as reaching the step after it.
 */
function joinsOf(wiring: Wiring): { join: (number | null)[]; kept: boolean[] } {
    const { steps } = wiring;

    /* Which steps sit inside which loop's body, so an edge from inside back
       to that loop can be read as the end of a pass rather than a re-entry. */
    const inside = new Map<number, Set<number>>();
    steps.forEach((_, loop) => {
        const start = wiring.body[loop];
        if (start === undefined) return;
        const seen = new Set<number>();
        const stack = [start];
        while (stack.length) {
            const at = stack.pop();
            if (at === undefined || at === loop || seen.has(at)) continue;
            seen.add(at);
            stack.push(...targetsOf(wiring, at));
        }
        inside.set(loop, seen);
    });

    /* Where each step can go next, for this purpose: a loop's body is its
       own region, so a loop goes only to what follows it. */
    const leaving = (from: number, to: number | undefined) =>
        to === undefined || inside.get(to)?.has(from) ? EXIT : to;
    const after = steps.map((step, at) => {
        if (!step.id) return [];
        const out: number[] = wiring.rules[at].map((rule) => leaving(at, rule.to));
        const next = wiring.next[at];
        /* A decision with no edge: the no-match way out, left out on purpose. */
        if (next !== undefined || step.kind !== "DECISION" || out.length === 0) out.push(leaving(at, next));
        return [...new Set(out)];
    });

    const reaches = steps.map(() => false);
    for (let moved = true; moved; ) {
        moved = false;
        steps.forEach((_, at) => {
            if (!reaches[at] && after[at].some((to) => to === EXIT || reaches[to])) {
                reaches[at] = true;
                moved = true;
            }
        });
    }

    const live = steps.map((_, at) => at).filter((at) => reaches[at]);
    const below = steps.map((_, at) => new Set(reaches[at] ? live : []));
    /* What every way out passes through, or null when none of them reach the
       end at all. A way out that never ends constrains nothing. */
    const meet = (from: number[]): Set<number> | null => {
        if (from.includes(EXIT)) return new Set();
        const sets = from.filter((to) => reaches[to]).map((to) => below[to]);
        if (sets.length === 0) return null;
        return new Set([...sets[0]].filter((one) => sets.every((set) => set.has(one))));
    };
    for (let moved = true; moved; ) {
        moved = false;
        for (const at of live) {
            const settled = meet(after[at]) ?? new Set<number>();
            settled.add(at);
            if (settled.size !== below[at].size) {
                below[at] = settled;
                moved = true;
            }
        }
    }

    /* The nearest of what every arm passes through: the one with the most
       steps still below it. */
    const join = steps.map((step, at) => {
        if (step.kind !== "DECISION" || !reaches[at]) return null;
        const common = meet(after[at]);
        if (!common) return null;
        let nearest: number | null = null;
        for (const one of common) {
            if (one === at) continue;
            if (nearest === null || below[one].size > below[nearest].size) nearest = one;
        }
        return nearest;
    });

    /* How many steps an arm holds before it ends: everything it reaches
       without passing back through the decision or out of the loop the
       decision is in. Null for an arm that leads back to the decision. */
    const size = (decision: number, from: number): number | null => {
        const stops = new Set([...inside].filter(([, body]) => body.has(decision)).map(([loop]) => loop));
        const seen = new Set<number>();
        const stack = [from];
        while (stack.length) {
            const at = stack.pop();
            if (at === undefined || stops.has(at) || seen.has(at)) continue;
            if (at === decision) return null;
            seen.add(at);
            stack.push(...targetsOf(wiring, at));
        }
        return seen.size;
    };

    const kept = steps.map(() => false);
    steps.forEach((step, at) => {
        if (step.kind !== "DECISION" || join[at] !== null || wiring.rules[at].length === 0) return;
        const ways = [...new Set([...wiring.rules[at].map((rule) => rule.to), wiring.next[at]])]
            .filter((to): to is number => to !== undefined);
        const sized = ways.map((to) => ({ to, steps: size(at, to) ?? 0 })).sort((left, right) => right.steps - left.steps);
        /* Only a clear winner. Two arms the same size are an either/or, and
           keeping one would make the other look like the exception. */
        if (sized.length < 2 || sized[0].steps === 0 || sized[0].steps === sized[1].steps) return;
        join[at] = sized[0].to;
        kept[at] = true;
    });

    return { join, kept };
}

/** `x == 'a'`, `x == 'b'`, … all on the same `x`: a switch, said once.
 *
 *  Only a raw string or a JSON literal counts as the value; a double-quoted
 *  JMESPath token is an identifier, not a string. The subject may hold `||`
 *  only inside brackets: comparison binds tighter than `||` and `&&`, so a
 *  bare one would mean the comparison was only ever half the condition.
 */
export function switchOf(conditions: string[]): { on: string; values: string[] } | null {
    if (conditions.length < 2) return null;
    const parts = conditions.map((one) => /^\s*(.+?)\s*==\s*('[^']*'|`[^`]*`)\s*$/.exec(one));
    const on = parts[0]?.[1];
    if (!on || /==|!=|&&|\|\||[<>]/.test(on.replace(/\([^()]*\)/g, ""))) return null;
    if (parts.some((one) => !one || one[1] !== on)) return null;
    return { on, values: parts.map((one) => tidy(one?.[2] ?? "")) };
}

/** A condition as it should read: JMESPath's backticks round `true`, `null`
 *  and numbers are syntax nobody reading needs. */
export function tidy(condition: string): string {
    return condition.replace(/`(true|false|null|-?\d+(?:\.\d+)?)`/g, "$1");
}

/** One path, until it ends, reaches where its fork meets again, or reaches
 *  a step already drawn.
 *
 *  `joins` holds the meeting points of every fork this path is inside, the
 *  innermost last. Reaching the innermost is the arm finishing, and the step
 *  is drawn once, after the fork. Reaching an outer one skips the rest of the
 *  inner fork, so that is said. `path` is every step drawn above this point
 *  on the way here, which is what makes a jump a jump back.
 */
function walk(wiring: Wiring, from: number | undefined, joins: number[], loop: number | null, path: number[]): FlowItem[] {
    const items: FlowItem[] = [];
    const above = [...path];
    let at = from;
    while (at !== undefined) {
        if (at === joins[joins.length - 1]) return items;
        if (at === loop) return items.length ? items : [{ type: "end", inLoop: true }];
        const step = wiring.steps[at];
        if (wiring.placed.has(at) || joins.includes(at)) {
            items.push({ type: "jump", to: step, id: step.id, back: above.includes(at) });
            return items;
        }
        wiring.placed.add(at);
        above.push(at);

        if (step.kind === "DECISION" && wiring.rules[at].length) {
            const join = wiring.join[at];
            const inner = join === null ? joins : [...joins, join];
            /* A kept arm is the rest of this line, so it is walked first: a
               side trip that reaches into it then points on to it rather
               than taking its steps away from the main line. */
            const rest = join !== null && wiring.kept[at] ? walk(wiring, join, joins, loop, above) : null;
            const switched = switchOf(wiring.rules[at].map((rule) => rule.when));
            const arms: FlowArm[] = wiring.rules[at].map((rule, one) => ({
                when: switched ? switched.values[one] : tidy(rule.when),
                items: rule.to === undefined
                    ? [{ type: "jump", to: null, id: rule.id, back: false }]
                    : walk(wiring, rule.to, inner, loop, above),
            }));
            const fallThrough = wiring.next[at];
            arms.push({
                when: null,
                items: fallThrough === undefined ? [{ type: "end", inLoop: loop !== null }] : walk(wiring, fallThrough, inner, loop, above),
            });
            items.push({ type: "fork", step, arms, then: join === null ? null : wiring.steps[join], on: switched?.on ?? null });
            if (rest) return [...items, ...rest];
            if (join === null) return items;
            at = join;
            continue;
        }

        if (step.kind === "LOOP" && wiring.body[at] !== undefined) {
            items.push({ type: "loop", step, body: walk(wiring, wiring.body[at], [], at, above) });
        } else {
            items.push({ type: "step", step });
        }
        at = wiring.next[at];
    }
    /* The chain ran out inside a fork that has a step after it. Drawn
       silently it would look like it carried on to that step — unless it
       ran out on an End, whose own row already says so. */
    const last = items[items.length - 1];
    const ended = last?.type === "step" && last.step.kind === "END";
    if (joins.length && !ended) items.push({ type: "end", inLoop: loop !== null });
    return items;
}

/* ── the whole thing ───────────────────────────────────────────────── */

export interface WorkflowShape {
    name: string;
    description: string | null;
    active: boolean;
    start: StartLine;
    ordered: FlowStep[];
    orphans: FlowStep[];
    /** The same steps as `ordered`, laid out with their branches. */
    flow: FlowItem[];
    /** The same steps as `orphans`, one outline per chain nothing reaches. */
    loose: FlowItem[][];
    trouble: string | null;
    /** What the server says this caller may do to it. */
    may: string[];
}

/** One workflow, opened.
 *
 *  Keyed on `name` like everything else here: every workflow endpoint in the
 *  SDK takes a name (`namespaces/workflows.d.ts`), so a payload that came back
 *  without one is not a workflow this app can do anything with.
 */
export function readShape(raw: unknown): WorkflowShape | null {
    if (!isRecord(raw)) return null;
    const name = str(raw.name);
    if (!name) return null;

    const nodes = Array.isArray(raw.nodes) ? raw.nodes : [];
    const steps = nodes.map(readFlowStep);
    const edges = readEdges(raw.edges);
    const { ordered, orphans, trouble } = orderSteps(steps, edges, str(raw.entry_node_id));
    /* The run order always opens on the first step, so the outline starts
       from the same one rather than deciding it a second time. */
    const { flow, loose } = outline(steps, edges, ordered.length ? steps.indexOf(ordered[0]) : -1);

    return {
        name,
        description: str(raw.description),
        active: raw.is_active !== false,
        start: readStart(raw.start),
        ordered,
        orphans,
        flow,
        loose,
        trouble,
        may: Array.isArray(raw.allowed_actions) ? raw.allowed_actions.filter((one): one is string => typeof one === "string") : [],
    };
}
