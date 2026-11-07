/** The plan file, as the `/o` frame reads and writes it: the plan section of
 *  `lemma-frontend/src/outcomes/model.ts` on branch `lemma/single-pod-exposure-8144fd`
 *  (at 20c8cb2dd), unchanged. That frame is not on main yet, so no test holds
 *  this to it: when it lands, copy the section across again. */

import { CORE_SCHEMA, dump, load } from "js-yaml";

/* ── the plan ─────────────────────────────────────────────────────────── */

/** Where an outcome's plan lives: a pod file people and Lemma both edit. */
export const PLAN_PATH = "/plan.md";

export type PlanSection = "must" | "how" | "who" | "never";

export const PLAN_SECTIONS: readonly PlanSection[] = ["must", "how", "who", "never"];

export const SECTION_TITLES: Record<PlanSection, string> = {
    must: "What must be true",
    how: "How we work",
    who: "Who holds what",
    never: "Never",
};

export interface PlanLine {
    text: string;
    /** Who added the line, as written after it. */
    by?: string;
    /** When, as written: "Oct 14". */
    on?: string;
}

export interface Plan {
    /** The sentence Lemma owns: "Every customer hears back within a day". */
    outcome: string;
    /** A bar held every week, or a finish line by a date. */
    kind: "week" | "date" | null;
    /** As written: "90%", "22 steps". */
    target: string | null;
    /** `YYYY-MM-DD`, for a finish line. */
    due: string | null;
    /** A read-only SQL query over this pod's tables that answers one number. */
    measure: string | null;
    /** `%` when the measure is a share. */
    unit: string | null;
    sections: Record<PlanSection, PlanLine[]>;
    /** The file's text as it was read, when it was: a save edits that text
     *  rather than replacing it, and refuses when the file has moved on. */
    file?: string;
}

export function emptyPlan(outcome = ""): Plan {
    return { outcome, kind: null, target: null, due: null, measure: null, unit: null, sections: { must: [], how: [], who: [], never: [] } };
}

function sectionOf(heading: string): PlanSection | null {
    const said = heading.toLowerCase();
    if (said.includes("never")) return "never";
    if (said.includes("who")) return "who";
    if (said.includes("how")) return "how";
    if (said.includes("must") || said.includes("true") || said.includes("outcome")) return "must";
    return null;
}

/** "Refunds over $200 need Dev — Priya, Oct 14" → the line and who/when. */
function lineOf(raw: string): PlanLine {
    const said = raw.trim();
    const match = /^(.*\S)\s+(?:—|--)\s+([^,—]+),\s*([^,—]+)$/.exec(said);
    if (!match) return { text: said };
    return { text: match[1].trim(), by: match[2].trim(), on: match[3].trim() };
}

function text(value: unknown): string | null {
    if (typeof value === "number") return String(value);
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

const HEADING = /^#{2,3}\s+(.*)$/;
const ITEM = /^(\s*[-*]\s+)(.*)$/;
const META_KEYS = ["outcome", "kind", "target", "due", "unit", "measure"] as const;

/** A plan file cut at its front matter: the raw block with its fences, what
 *  is inside them, and the rest. A file with no closed block is all body. */
function splitFront(source: string): { head: string; front: string | null; body: string } {
    const all = source.replace(/\r\n/g, "\n");
    if (!all.startsWith("---\n")) return { head: "", front: null, body: all };
    const end = all.indexOf("\n---", 4);
    if (end <= 0) return { head: "", front: null, body: all };
    return { head: all.slice(0, end + 4), front: all.slice(4, end), body: all.slice(end + 4) };
}

/** The front matter as a map, or null when it will not parse. The core
 *  schema keeps an unquoted `due: 2026-10-30` the string it was written as,
 *  where the default one would read a Date and the plan would lose it. */
function loadFront(front: string): Record<string, unknown> | null {
    try {
        const loaded = load(front, { schema: CORE_SCHEMA });
        if (loaded === null || loaded === undefined) return {};
        return typeof loaded === "object" && !Array.isArray(loaded) ? (loaded as Record<string, unknown>) : null;
    } catch {
        return null;
    }
}

/** Reads a plan written by a person, by Lemma, or by this frame. Forgiving on
 *  purpose: an unknown heading is skipped, not fatal, and front matter that
 *  will not parse leaves the lines readable. */
export function parsePlan(source: string): Plan {
    const plan = emptyPlan();
    const { front, body } = splitFront(source);
    if (front !== null) {
        const meta = loadFront(front) ?? {};
        plan.outcome = text(meta.outcome) ?? "";
        const kind = text(meta.kind);
        plan.kind = kind === "week" || kind === "date" ? kind : null;
        plan.target = text(meta.target);
        plan.due = text(meta.due);
        plan.measure = text(meta.measure);
        plan.unit = text(meta.unit);
    }
    let current: PlanSection | null = null;
    for (const raw of body.split("\n")) {
        const heading = HEADING.exec(raw);
        if (heading) {
            current = sectionOf(heading[1]);
            continue;
        }
        const item = ITEM.exec(raw);
        if (item && current && item[2].trim()) plan.sections[current].push(lineOf(item[2]));
    }
    if (!plan.outcome && plan.sections.must[0]) plan.outcome = plan.sections.must[0].text;
    return plan;
}

const PREAMBLE = "<!-- The plan for what this Lemma owns. People and the Lemma both edit it: every line ends with who added it and when. The measure is one read-only SQL query that answers a single number. -->";

function metaOf(plan: Plan): Record<string, string> {
    const meta: Record<string, string> = {};
    for (const key of META_KEYS) {
        const value = plan[key];
        if (value) meta[key] = value;
    }
    return meta;
}

function written(line: PlanLine): string {
    const by = line.by && line.on ? ` — ${line.by}, ${line.on}` : "";
    return `${line.text}${by}`;
}

function sameLine(a: PlanLine, b: PlanLine): boolean {
    return a.text === b.text && a.by === b.by && a.on === b.on;
}

/** The steps from a section's old lines to its new ones, as the longest run
 *  they share: each old line kept or dropped, and the new lines that follow
 *  each (-1: before the first). An edited line is a drop and an add in the
 *  same place, so it stays where it was. */
function alignLines(old: readonly PlanLine[], next: readonly PlanLine[]): { kept: boolean[]; after: Map<number, PlanLine[]> } {
    const shared = Array.from({ length: old.length + 1 }, () => new Array<number>(next.length + 1).fill(0));
    for (let i = old.length - 1; i >= 0; i -= 1) {
        for (let j = next.length - 1; j >= 0; j -= 1) {
            shared[i][j] = sameLine(old[i], next[j]) ? shared[i + 1][j + 1] + 1 : Math.max(shared[i + 1][j], shared[i][j + 1]);
        }
    }
    const kept = new Array<boolean>(old.length).fill(false);
    const after = new Map<number, PlanLine[]>();
    const add = (at: number, line: PlanLine) => after.set(at, [...(after.get(at) ?? []), line]);
    let i = 0;
    let j = 0;
    while (i < old.length || j < next.length) {
        if (i < old.length && j < next.length && sameLine(old[i], next[j])) {
            kept[i] = true;
            i += 1;
            j += 1;
        } else if (i < old.length && (j >= next.length || shared[i + 1][j] >= shared[i][j + 1])) {
            i += 1;
        } else {
            add(i - 1, next[j]);
            j += 1;
        }
    }
    return { kept, after };
}

/** The new front matter block, or the old one untouched when the plan's
 *  own keys did not change, so a line edit never reformats it. */
function mergedHead(plan: Plan, before: Plan, head: string, front: string | null): string {
    const meta = metaOf(plan);
    const was = metaOf(before);
    if (META_KEYS.every((key) => meta[key] === was[key])) return head;
    if (front === null) return Object.keys(meta).length ? "---\n" + dump(meta, { lineWidth: -1 }) + "---\n\n" : "";
    const kept = loadFront(front);
    if (kept === null) throw new Error("plan.md's front matter doesn't parse, so saving the measure would lose it. Fix it in plan.md first.");
    for (const key of META_KEYS) {
        if (meta[key]) kept[key] = meta[key];
        else delete kept[key];
    }
    return "---\n" + dump(kept, { lineWidth: -1 }) + "---";
}

/** Writes the plan. Given the file it was read from, it edits that file in
 *  place: only the lines and front matter keys the plan holds change, and
 *  everything the parser does not read -- a title, prose, another heading,
 *  another key -- stays as it was written. */
export function serializePlan(plan: Plan, onto?: string | null): string {
    if (onto) return mergePlan(plan, onto);
    const meta = metaOf(plan);
    const front = Object.keys(meta).length ? "---\n" + dump(meta, { lineWidth: -1 }) + "---\n\n" : "";
    const sections = PLAN_SECTIONS.map((section) => {
        const lines = plan.sections[section].map((line) => `- ${written(line)}`);
        return `## ${SECTION_TITLES[section]}\n${lines.join("\n")}`;
    });
    return `${front}${PREAMBLE}\n\n${sections.join("\n\n")}\n`;
}

function mergePlan(plan: Plan, onto: string): string {
    const before = parsePlan(onto);
    const { head, front, body } = splitFront(onto);
    const aligned = Object.fromEntries(PLAN_SECTIONS.map((section) => [section, alignLines(before.sections[section], plan.sections[section])])) as Record<PlanSection, ReturnType<typeof alignLines>>;
    const seen: Record<PlanSection, number> = { must: 0, how: 0, who: 0, never: 0 };
    const headingAt: Partial<Record<PlanSection, number>> = {};
    const out: string[] = [];
    let current: PlanSection | null = null;
    for (const raw of body.split("\n")) {
        const heading = HEADING.exec(raw);
        if (heading) {
            current = sectionOf(heading[1]);
            out.push(raw);
            if (current && headingAt[current] === undefined) headingAt[current] = out.length - 1;
            continue;
        }
        const item = ITEM.exec(raw);
        if (!item || !current || !item[2].trim()) {
            out.push(raw);
            continue;
        }
        const { kept, after } = aligned[current];
        const index = seen[current];
        seen[current] += 1;
        if (index === 0) out.push(...(after.get(-1) ?? []).map((line) => `- ${written(line)}`));
        if (kept[index]) out.push(raw);
        /* A replaced line keeps the bullet and indent it was written with. */
        out.push(...(after.get(index) ?? []).map((line, at) => `${at === 0 && !kept[index] ? item[1] : "- "}${written(line)}`));
    }
    /* Lines for a section that had none: under its heading when the file has
       one, else under a new heading at the end. Inserted from the bottom up,
       so earlier positions hold. */
    const empty = PLAN_SECTIONS.filter((section) => before.sections[section].length === 0 && (aligned[section].after.get(-1) ?? []).length > 0);
    const under = empty.filter((section) => headingAt[section] !== undefined).sort((a, b) => (headingAt[b] as number) - (headingAt[a] as number));
    for (const section of under) {
        out.splice((headingAt[section] as number) + 1, 0, ...(aligned[section].after.get(-1) ?? []).map((line) => `- ${written(line)}`));
    }
    for (const section of empty.filter((one) => headingAt[one] === undefined)) {
        while (out.length && !out[out.length - 1].trim()) out.pop();
        out.push("", `## ${SECTION_TITLES[section]}`, ...(aligned[section].after.get(-1) ?? []).map((line) => `- ${written(line)}`), "");
    }
    return mergedHead(plan, before, head, front) + out.join("\n");
}

export interface DraftInput {
    description?: string | null;
    schedules: readonly { title: string; trigger: string; active: boolean; internal: boolean }[];
    agents: readonly { label: string; blurb: string; front: boolean }[];
    skills: readonly string[];
}

function lowerFirst(value: string): string {
    return value ? value.charAt(0).toLowerCase() + value.slice(1) : value;
}

/** A first plan, read off what the pod already runs, for a pod with no plan
 *  file yet. Nothing invented: every line names something that exists. */
export function draftPlan(input: DraftInput): Plan {
    const plan = emptyPlan(input.description?.trim() ?? "");
    if (plan.outcome) plan.sections.must.push({ text: plan.outcome });
    for (const name of input.skills.slice(0, 6)) plan.sections.how.push({ text: `Follows the ${name} skill` });
    for (const job of input.schedules) {
        if (!job.active || job.internal) continue;
        plan.sections.who.push({ text: `${job.title} · ${lowerFirst(job.trigger)}` });
    }
    for (const agent of input.agents) {
        if (agent.front) continue;
        plan.sections.who.push({ text: agent.blurb ? `${agent.label}: ${lowerFirst(agent.blurb)}` : agent.label });
    }
    return plan;
}

/** The number a measure answered: the first finite value in its first row. */
export function readMeasure(rows: readonly Record<string, unknown>[]): number | null {
    const first = rows[0];
    if (!first) return null;
    for (const value of Object.values(first)) {
        const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : Number.NaN;
        if (Number.isFinite(number)) return number;
    }
    return null;
}

export function formatMeasure(value: number, unit: string | null): string {
    const rounded = Math.abs(value) >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
    return unit === "%" ? `${rounded}%` : String(rounded);
}

/** Whether an outcome is holding its bar: only for a weekly outcome whose
 *  plan names a target and whose measure answered. A finish line has no pace
 *  to compare against yet, so it says nothing rather than guess. */
export function standingOf(plan: Plan | null | undefined, value: number | null | undefined): "on" | "behind" | null {
    if (!plan || value === null || value === undefined || !plan.target || plan.kind === "date") return null;
    const target = Number.parseFloat(plan.target);
    if (!Number.isFinite(target)) return null;
    return value >= target ? "on" : "behind";
}

/** "Oct 14", the stamp a line is written with. */
export function stampOf(at: Date): string {
    return at.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/* From the same branch's `outcomes/data.ts`, unchanged. */

/** What "Ask Lemma to write the plan" sends: the format and the rules, so
 *  the agent's file reads back here line for line. */
export function planRequest(outcome: string): string {
    return [
        `Write the plan for what you own to ${PLAN_PATH}${outcome ? `: "${outcome}"` : ""}.`,
        "Use front matter with outcome (the one sentence you own: a bar held every week, or a finish line by a date), kind (week or date), target (like 90%), due (YYYY-MM-DD, only for a date), unit (% for a share) and measure: one read-only SQL query over this pod's tables that returns a single number.",
        "Quote target, unit and due in double quotes, as in unit: \"%\" and due: \"2026-11-30\": unquoted, a % does not parse and a date may be read as something other than the text you wrote.",
        "Then four sections: ## What must be true, ## How we work, ## Who holds what, ## Never. One line per bullet, each ending with \" — Lemma, <Mon D>\".",
        "Only write lines that name something that exists here or that you are about to set up. If you build anything for this, say what you built in your reply.",
    ].join(" ");
}
