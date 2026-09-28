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
