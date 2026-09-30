/** A run, drawn in the shape of its workflow.
 *
 *  `shape.ts` flattens a workflow into the order a run meets its steps, which
 *  is right for "what does this do" and wrong for "what did this run do": a
 *  decision has arms, and a run takes one of them. Printed flat, the arm it
 *  did not take reads as a step it skipped for no reason, and a loop's body
 *  reads as two steps that happened once.
 *
 *  So this builds the tree the editor would draw — a decision with its arms
 *  nested under it, a loop with its body — and hangs every `step_history`
 *  entry on the node it belongs to. The page then only has to say, per node,
 *  what happened there.
 *
 *  Two facts about the payload shape this. A decision's arms are
 *  `config.rules[].next_node_id`, not edges; its one outgoing edge is the
 *  default for when no rule matched (`domain/nodes/decision.py`). A loop's
 *  body is `config.child_node_id` and the body's last step edges back to the
 *  loop (`domain/nodes/loop.py`). Neither throws here, and nothing is dropped:
 *  a node the walk never reaches is appended at the end rather than lost.
 */

import { isRecord, str, type StepRow } from "./runs";

export interface GraphNode {
    id: string;
    kind: string;
    label: string | null;
    config: Record<string, unknown>;
}

export interface Graph {
    nodes: Map<string, GraphNode>;
    /** Plain edges, source → targets, in the order they arrived. */
    edges: Map<string, string[]>;
    /** Every node id, in the order they arrived, for anything the walk misses. */
    order: string[];
}

export interface Arm {
    /** "Rule 1", or the author's own label for the target, or "Otherwise". */
    label: string;
    /** The rule's condition; null for the default arm. */
    condition: string | null;
    items: TreeItem[];
}

export type TreeItem =
    | { type: "step"; id: string }
    | { type: "decision"; id: string; arms: Arm[] }
    | { type: "loop"; id: string; body: TreeItem[] };

export function readGraph(raw: unknown): Graph | null {
    if (!isRecord(raw)) return null;
    const nodes = new Map<string, GraphNode>();
    const order: string[] = [];
    for (const one of Array.isArray(raw.nodes) ? raw.nodes : []) {
        if (!isRecord(one)) continue;
        const id = str(one.id);
        if (!id || nodes.has(id)) continue;
        nodes.set(id, {
            id,
            kind: (str(one.type) ?? "").toUpperCase(),
            label: str(one.label),
            config: isRecord(one.config) ? one.config : {},
        });
        order.push(id);
    }
    const edges = new Map<string, string[]>();
    for (const one of Array.isArray(raw.edges) ? raw.edges : []) {
        if (!isRecord(one)) continue;
        const source = str(one.source);
        const target = str(one.target);
        if (!source || !target || !nodes.has(source) || !nodes.has(target)) continue;
        edges.set(source, [...(edges.get(source) ?? []), target]);
    }
    return { nodes, edges, order };
}

function rulesOf(node: GraphNode): { condition: string | null; next: string }[] {
    const rules = Array.isArray(node.config.rules) ? node.config.rules : [];
    return rules
        .map((rule) => (isRecord(rule) ? { condition: str(rule.condition), next: str(rule.next_node_id) ?? "" } : null))
        .filter((rule): rule is { condition: string | null; next: string } => Boolean(rule && rule.next));
}

function bodyOf(node: GraphNode): string | null {
    return node.kind === "LOOP" ? str(node.config.child_node_id) : null;
}

/** Everything a node can hand control to: edges, rule targets, a loop body. */
function successors(graph: Graph, id: string): string[] {
    const node = graph.nodes.get(id);
    const out = [...(graph.edges.get(id) ?? [])];
    if (node?.kind === "DECISION") out.push(...rulesOf(node).map((rule) => rule.next));
    const body = node ? bodyOf(node) : null;
    if (body) out.push(body);
    return out.filter((next) => graph.nodes.has(next));
}

function reach(graph: Graph, from: string): Set<string> {
    const seen = new Set<string>();
    const queue = [from];
    while (queue.length) {
        const at = queue.shift()!;
        if (seen.has(at) || !graph.nodes.has(at)) continue;
        seen.add(at);
        queue.push(...successors(graph, at));
    }
    return seen;
}

/** The node nothing else points at — the validator's rule for the first step. */
export function entryOf(graph: Graph): string | null {
    const pointed = new Set<string>();
    for (const id of graph.order) for (const next of successors(graph, id)) pointed.add(next);
    return graph.order.find((id) => !pointed.has(id)) ?? graph.order[0] ?? null;
}

/** Where a decision's arms meet again: the node reachable from the most arms
 *  (at least two), nearest the decision. Null when every arm ends on its own. */
function joinOf(graph: Graph, starts: string[], distance: Map<string, number>): string | null {
    if (starts.length < 2) return null;
    const reaches = starts.map((start) => reach(graph, start));
    let best: string | null = null;
    let bestCount = 1;
    let bestDistance = Infinity;
    for (const id of graph.order) {
        const count = reaches.filter((set) => set.has(id)).length;
        const far = distance.get(id) ?? Infinity;
        if (count > bestCount || (count === bestCount && count >= 2 && far < bestDistance)) {
            best = id;
            bestCount = count;
            bestDistance = far;
        }
    }
    return bestCount >= 2 ? best : null;
}

/** Shortest hop count from the entry, used to pick the nearest join. */
function distances(graph: Graph, entry: string | null): Map<string, number> {
    const out = new Map<string, number>();
    if (!entry) return out;
    const queue: [string, number][] = [[entry, 0]];
    while (queue.length) {
        const [at, hops] = queue.shift()!;
        if (out.has(at)) continue;
        out.set(at, hops);
        for (const next of successors(graph, at)) queue.push([next, hops + 1]);
    }
    return out;
}

export function buildTree(graph: Graph): TreeItem[] {
    const entry = entryOf(graph);
    const distance = distances(graph, entry);
    const placed = new Set<string>();

    const walk = (start: string | null, stops: Set<string>): TreeItem[] => {
        const items: TreeItem[] = [];
        let at = start;
        while (at && !stops.has(at) && !placed.has(at) && graph.nodes.has(at)) {
            const node = graph.nodes.get(at)!;
            placed.add(at);
            if (node.kind === "DECISION") {
                const rules = rulesOf(node);
                const fallback = graph.edges.get(at)?.[0] ?? null;
                const starts = [...rules.map((rule) => rule.next), ...(fallback ? [fallback] : [])];
                const join = joinOf(graph, starts, distance);
                const inner = new Set(stops);
                if (join) inner.add(join);
                const arms: Arm[] = rules.map((rule, index) => ({
                    label: graph.nodes.get(rule.next)?.label ?? "Rule " + (index + 1),
                    condition: rule.condition,
                    items: walk(rule.next, inner),
                }));
                if (fallback) arms.push({ label: "Otherwise", condition: null, items: walk(fallback, inner) });
                items.push({ type: "decision", id: at, arms });
                at = join;
                continue;
            }
            if (node.kind === "LOOP") {
                const body = bodyOf(node);
                const inner = new Set(stops);
                inner.add(at);
                items.push({ type: "loop", id: at, body: body ? walk(body, inner) : [] });
                at = graph.edges.get(at)?.[0] ?? null;
                continue;
            }
            items.push({ type: "step", id: at });
            at = graph.edges.get(at)?.[0] ?? null;
        }
        return items;
    };

    const tree = walk(entry, new Set());
    /* Nothing is lost: a step the walk never reached is still a step. */
    for (const id of graph.order) if (!placed.has(id)) tree.push(...walk(id, new Set()));
    return tree;
}

/* ── what happened at each node ────────────────────────────────────── */

export type NodeState = "done" | "failed" | "running" | "waiting" | "skipped" | "pending" | "cancelled";

/** Every history entry for a node, in the order they ran. */
export function tracesByNode(steps: StepRow[]): Map<string, StepRow[]> {
    const out = new Map<string, StepRow[]>();
    for (const step of steps) {
        if (!step.nodeId) continue;
        out.set(step.nodeId, [...(out.get(step.nodeId) ?? []), step]);
    }
    return out;
}

export function stateOfTrace(status: string): NodeState {
    switch (status.toUpperCase()) {
        case "COMPLETED": return "done";
        case "FAILED": return "failed";
        case "WAITING": return "waiting";
        case "RUNNING": case "EXECUTING": case "PENDING": return "running";
        case "CANCELLED": return "cancelled";
        default: return "done";
    }
}

/** A node with no history: never reached. Whether that is "not taken" or
 *  "not yet" depends on whether the run can still get there. */
export function stateOfUnvisited(runGoing: boolean): NodeState {
    return runGoing ? "pending" : "skipped";
}

/** Every node id inside a list of items, arms and bodies included. */
export function idsIn(items: TreeItem[]): string[] {
    const out: string[] = [];
    for (const item of items) {
        out.push(item.id);
        if (item.type === "decision") for (const arm of item.arms) out.push(...idsIn(arm.items));
        if (item.type === "loop") out.push(...idsIn(item.body));
    }
    return out;
}

/** Steps that ran, of the steps a run can run — END is not a step anybody
 *  waits on, so it counts toward neither side. */
export function progressOf(graph: Graph, steps: StepRow[]): { done: number; total: number } {
    const counted = graph.order.filter((id) => graph.nodes.get(id)?.kind !== "END");
    const finished = new Set(steps.filter((step) => step.status.toUpperCase() === "COMPLETED").map((step) => step.nodeId));
    return { done: counted.filter((id) => finished.has(id)).length, total: counted.length };
}

/** The first sentence a step's output offers, from the keys people and agents
 *  conventionally put one under. */
export function leadOf(output: unknown): string | null {
    if (typeof output === "string") return output.trim() || null;
    if (!isRecord(output)) return null;
    for (const key of ["summary", "note", "answer", "message", "text", "content", "description", "result"]) {
        const value = output[key];
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    return null;
}
