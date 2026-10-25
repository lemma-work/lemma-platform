import type {
    ConnectorChoice,
    ConnectorPlan,
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
    CONNECTOR: { one: "Connection", many: "Connections", order: 9 },
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

/** `app_slug` → "App slug", `triage-channel` → "Triage channel". */
export function humanize(name: string): string {
    const words = name
        .replace(/[_-]+/g, " ")
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .trim()
        .toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The app a bundle installs, if it installs one.
 *
 *  An APP step is named by the app's own directory, which is the segment a
 *  workspace URL names it by, so the step carries everything the landing
 *  address needs. The first one is the one the bundle is built around; a
 *  bundle with several is rare and either is better than the teammate. */
export function installedApp(steps: PlanStep[]): string | null {
    return steps.find((step) => step.kind === "APP")?.name ?? null;
}

/** Where somebody lands once an install has finished.
 *
 *  A teammate that ships an app is meant to be used from the app: the app is
 *  the work, and the conversation behind it is where it was built. Landing on
 *  the teammate instead puts the thing that was just installed behind a second
 *  click, which reads as an install that produced nothing. */
export function landingPath(podId: string, steps: PlanStep[]): string {
    const base = "/t/" + encodeURIComponent(podId);
    const app = installedApp(steps);
    return app ? base + "/app/" + encodeURIComponent(app) : base;
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
 *  A free variable is only ever a name (an app's slug, say), and the teammate's
 *  name is the one thing the person has already chosen. Deriving it is what
 *  lets the plain path install without asking a non-technical person what a
 *  slug is; the developer path shows the same value in an editable field. */
export function suggestedValue(variable: VariableSpec, who: string): string {
    if (variable.default) return variable.default;
    if (variable.kind !== "free") return "";
    return slugify(who);
}

/** Whether the plain path has to put this variable in front of the person:
 *  because the name we would have derived came out empty. */
export function needsAnswer(variable: VariableSpec, who: string): boolean {
    return !suggestedValue(variable, who);
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

/** What the installer has picked for one bundled connector.
 *
 *  `installation` is an installation id, `CREATE` to install Lemma's own app
 *  for it, or empty when there is nothing to pick from. `share` is one of the
 *  installer's own account ids, or empty to share none — a credential is only
 *  ever shared on purpose, so empty is the default. */
export interface ConnectorPick {
    installation: string;
    share: string;
}

export const CREATE_INSTALLATION = "create";

/** The pick a connector starts with: the org's default installation, else
 *  nothing (the installer has to choose to create one), sharing no account. */
export function defaultPick(c: ConnectorPlan): ConnectorPick {
    return { installation: c.auth_config_id ?? "", share: "" };
}

/** Whether the plan declares this connector at all — not one the pod
 *  already has, nor one whose slug clashes with the pod's. */
export function declares(c: ConnectorPlan): boolean {
    return c.status !== "EXISTS" && c.status !== "CONFLICT";
}

/** Why this connector stops the install as picked, or null when it does not.
 *
 *  A connector with nowhere to map to cannot be declared, and whatever names
 *  its slug would be bound to nothing; a shared one with no account only
 *  waits for one, which is a warning, not a stop. */
export function blockedBy(c: ConnectorPlan, pick: ConnectorPick): string | null {
    if (c.status === "CONFLICT") return c.message ?? "Its name is taken in this teammate.";
    if (!declares(c)) return null;
    if (pick.installation === CREATE_INSTALLATION)
        return c.can_create_installation ? null : c.message;
    if (pick.installation) return null;
    return c.message ?? "Your organization has no " + c.connector + " installation.";
}

/** Which connectors the installer is asked about: any that blocks, has more
 *  than one installation to pick from, could be created, or runs as one
 *  shared account. A per-member one with one installation has nothing to ask. */
export function askedConnectors(list: ConnectorPlan[]): ConnectorPlan[] {
    return list.filter(
        (c) =>
            c.status === "CONFLICT" ||
            (declares(c) &&
                (c.installations.length !== 1 ||
                    c.mode === "SHARED" ||
                    c.can_create_installation)),
    );
}

/** The accounts a shared connector can share on the picked installation. */
export function shareableAccounts(c: ConnectorPlan, pick: ConnectorPick) {
    return c.account_options.filter(
        (a) => a.auth_config_id === pick.installation,
    );
}

/** The apply request's `connectors`, by slug. Only what differs from the
 *  plan's own default is sent; the server fills in the rest. */
export function connectorChoices(
    list: ConnectorPlan[],
    picks: Record<string, ConnectorPick>,
): Record<string, ConnectorChoice> {
    const out: Record<string, ConnectorChoice> = {};
    for (const c of list) {
        if (!declares(c)) continue;
        const pick = picks[c.slug] ?? defaultPick(c);
        const choice: ConnectorChoice = {};
        if (pick.installation === CREATE_INSTALLATION)
            choice.create_installation = true;
        else if (pick.installation && pick.installation !== c.auth_config_id)
            choice.auth_config_id = pick.installation;
        if (pick.share && c.mode === "SHARED")
            choice.share_account_id = pick.share;
        if (Object.keys(choice).length > 0) out[c.slug] = choice;
    }
    return out;
}

/** One line on what a connector is and how it runs. */
export function connectorLine(c: ConnectorPlan): string {
    const how =
        c.mode === "SHARED"
            ? "one shared account for everyone"
            : c.required
              ? "each member's own account — members are asked to connect"
              : "each member's own account";
    return humanize(c.connector) + ", " + how + (c.purpose ? ". " + c.purpose : "");
}
