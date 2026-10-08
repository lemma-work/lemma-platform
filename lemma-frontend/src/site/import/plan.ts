import type {
    ImportStatus,
    PlanStep,
    StepAction,
    VariableSpec,
} from "../import-types";

/** How a plan step's kind reads to someone installing, singular and plural.
 *
 *  Grants are not listed on their own: they are the permissions an agent or
 *  function needs, and a row that says "Function grants: triage" asks the
 *  person to judge something they have no way to judge. They still install;
 *  they are just folded into the thing they belong to. */
const KINDS: Record<string, { one: string; many: string; order: number }> = {
    AGENT: { one: "Agent", many: "Agents", order: 0 },
    APP: { one: "App", many: "Apps", order: 1 },
    WORKFLOW: { one: "Workflow", many: "Workflows", order: 2 },
    SCHEDULE: { one: "Automation", many: "Automations", order: 3 },
    TABLE: { one: "Table", many: "Tables", order: 4 },
    TABLE_DATA: { one: "Sample rows", many: "Sample rows", order: 5 },
    FUNCTION: { one: "Function", many: "Functions", order: 6 },
    SURFACE: { one: "Channel", many: "Channels", order: 7 },
    FILE: { one: "File", many: "Files", order: 8 },
};
const HIDDEN = new Set(["AGENT_GRANTS", "FUNCTION_GRANTS"]);

export interface StepGroup {
    kind: string;
    label: string;
    steps: PlanStep[];
}

export function groupSteps(steps: PlanStep[]): StepGroup[] {
    const groups = new Map<string, PlanStep[]>();
    for (const step of steps) {
        if (HIDDEN.has(step.kind)) continue;
        groups.set(step.kind, [...(groups.get(step.kind) ?? []), step]);
    }
    return [...groups.entries()]
        .map(([kind, list]) => ({
            kind,
            label:
                list.length === 1
                    ? (KINDS[kind]?.one ?? humanize(kind))
                    : (KINDS[kind]?.many ?? humanize(kind)),
            steps: list,
        }))
        .sort(
            (a, b) =>
                (KINDS[a.kind]?.order ?? 99) - (KINDS[b.kind]?.order ?? 99),
        );
}

export function actionLabel(step: PlanStep): string {
    if (step.destructive) return "Replaces yours";
    const labels: Record<StepAction, string> = {
        CREATE: "New",
        UPDATE: "Updates yours",
        SKIP: "Already there",
    };
    return labels[step.action];
}

/** One line for where the job is, in the words of what it is doing. */
export function statusLine(status: ImportStatus): string {
    switch (status) {
        case "QUEUED":
            return "Getting started…";
        case "FETCHING":
            return "Reading the repository…";
        case "PLANNING":
            return "Working out what's included…";
        case "AWAITING_CONFIRMATION":
            return "Ready for your review";
        case "APPLYING":
            return "Installing…";
        case "COMPLETED":
            return "Installed";
        case "FAILED":
            return "The installation stopped";
        case "CANCELLED":
        case "PARTIALLY_CANCELLED":
            return "Cancelled";
    }
}

export const WORKING: ImportStatus[] = [
    "QUEUED",
    "FETCHING",
    "PLANNING",
    "APPLYING",
];

/** Variables the person has to answer. A `pod_member` fills itself in with
 *  whoever is installing, so asking about it would only be noise. */
export function askedVariables(variables: VariableSpec[]): VariableSpec[] {
    return variables.filter((v) => v.kind !== "pod_member");
}

/** `slack_account` → "Slack account", `triage-channel` → "Triage channel". */
export function humanize(name: string): string {
    const words = name
        .replace(/[_-]+/g, " ")
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .trim()
        .toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A repository slug as a teammate's name: `smart-inbox` → "Smart Inbox". */
export function teammateName(repo: string): string {
    return repo
        .replace(/[_-]+/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase())
        .trim();
}

/** A name as a machine reads it: `Smart Inbox` → `smart-inbox`. */
export function slugify(name: string): string {
    return name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

/** What a variable is worth when nobody has chosen a value.
 *
 *  An account cannot be invented — it belongs to whoever is installing — but a
 *  free variable is only ever a name (an app's slug, say), and the teammate's
 *  name is the one thing the person has already chosen. Deriving it is what
 *  lets the plain path install without asking a non-technical person what a
 *  slug is; the developer path shows the same value in an editable field. */
export function suggestedValue(variable: VariableSpec, who: string): string {
    if (variable.default) return variable.default;
    if (variable.kind !== "free") return "";
    return slugify(who);
}

/** Whether the plain path has to put this variable in front of the person:
 *  because nothing can fill it in for them, or because the name we would have
 *  derived came out empty. */
export function needsAnswer(variable: VariableSpec, who: string): boolean {
    return variable.kind === "account" || !suggestedValue(variable, who);
}

/** What a bundle brings, as one line: "2 agents, 1 workflow and 1 table".
 *
 *  The plain path says what is coming in a sentence rather than making a
 *  non-technical person read a component-by-component list; the list is still
 *  there, one press away. */
export function contentsLine(groups: StepGroup[]): string {
    const parts = groups.map(
        (group) => `${group.steps.length} ${group.label.toLowerCase()}`,
    );
    if (parts.length < 2) return parts.join("");
    return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
}
