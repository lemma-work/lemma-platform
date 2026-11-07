import { SAMPLE_LEMMAS } from "../../../data/sample";
import { templateOf } from "./model";
import type { InstallSource, Readme } from "./source";
import type { ImportPlan, ImportStatus, ImportStatusResponse, PlanStep, StepAction, VariableSpec } from "@web/site/import-types";
import type { PendingInstall } from "@web/site/import/pending-install";
import { slugify } from "@web/site/import/plan";

/** Templates, installing and remixing as sample data: a whole install in
 *  memory, never the network. A catalogue template reads a README written
 *  here and installs in a few seconds (a plan with an agent, an app, a
 *  workflow, an automation, a table and its sample rows, an account to
 *  choose and a name to fill in); any other repository has no README and
 *  stops while it is being read, in the server's own words, so every state
 *  can be reached.
 *
 *  Sample data has five Lemmas and cannot add a sixth to the lists other
 *  screens read, so "a new Lemma" stands in as Support under the name it
 *  was given: the journey's ends then open real sample screens (Support's
 *  Ticket queue, a new chat in Support). */

const [SUPPORT] = SAMPLE_LEMMAS;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/* How long each part of a sample job takes, in milliseconds. */
const QUEUED_MS = 600;
const FETCHED_MS = 1700;
const PLANNED_MS = 2700;
const STEP_MS = 650;

function sampleReadme(owner: string, repo: string): Readme | null {
    const template = templateOf(owner, repo);
    if (!template) return null;
    const slug = slugify(template.name);
    const markdown = [
        `# ${template.name}`,
        template.description,
        "## What it does",
        template.outcomes.map((one) => "- " + one).join("\n"),
        "## What is in this repository",
        "```\n" + [`apps/${slug}/`.padEnd(18) + "the app", "agents/".padEnd(18) + "who does the work", "workflows/".padEnd(18) + "what runs on its own", "tables/".padEnd(18) + "what it keeps"].join("\n") + "\n```",
        "## Make it yours",
        `Everything here is yours once it is installed. Ask the Lemma that has it to change the app, the agents or the workflows in a chat, or edit the files in [the repository](./apps/${slug}).`,
    ].join("\n\n");
    return { markdown, branch: "HEAD" };
}

interface Built {
    kind: string;
    name: string;
    action: StepAction;
    destructive?: boolean;
}

/** What a sample bundle brings. Frontdesk is the support template, and
 *  brings Support's own ticket queue; the rest bring parts named after
 *  themselves. Into a Lemma that has things already, one part updates
 *  what is there, one replaces a table, and one is there already. */
function samplePlan(repo: string, into: string | null): ImportPlan {
    const support = repo.toLowerCase() === "frontdesk";
    const slug = slugify(repo) || "app";
    const table = support ? "tickets" : "items";
    const existing = into !== null;
    const parts: Built[] = [
        { kind: "AGENT", name: support ? "desk" : slug, action: existing ? "UPDATE" : "CREATE" },
        { kind: "APP", name: support ? "ticket-queue" : slug, action: "CREATE" },
        { kind: "WORKFLOW", name: support ? "sort_new_messages" : "review_new_items", action: "CREATE" },
        { kind: "SCHEDULE", name: support ? "chase_unanswered" : "daily_tidy", action: "CREATE" },
        { kind: "TABLE", name: table, action: existing ? "UPDATE" : "CREATE", destructive: existing },
        { kind: "TABLE_DATA", name: table, action: "CREATE" },
        { kind: "FILE", name: support ? "playbooks/routing.md" : "notes/README.md", action: existing ? "SKIP" : "CREATE" },
        { kind: "AGENT_GRANTS", name: support ? "desk" : slug, action: "CREATE" },
    ];
    const steps: PlanStep[] = parts.map((part, index) => ({
        index,
        kind: part.kind,
        name: part.name,
        action: part.action,
        destructive: Boolean(part.destructive),
        detail: {},
        status: "PENDING",
        error: null,
    }));
    const variables: VariableSpec[] = [
        { name: "app_slug", kind: "free", description: "The address the app is published under.", required: true, default: null },
        { name: "owner", kind: "pod_member", description: null, required: true, default: null },
    ];
    return {
        format_version: 1,
        bundle_name: repo,
        steps,
        variables,
        /* The mailbox new messages come from: a connector the bundle brings,
           each member's own Gmail, with two installations to choose from. */
        connectors: [{
            slug: "inbox",
            connector: "gmail",
            mode: "PER_MEMBER",
            required: true,
            purpose: "The mailbox new messages come from.",
            visibility: "POD",
            status: "READY",
            installations: [
                { auth_config_id: "ac-gmail", name: "Gmail", is_default: true },
                { auth_config_id: "ac-gmail-support", name: "Gmail (support)", is_default: false },
            ],
            auth_config_id: "ac-gmail",
            can_create_installation: false,
            account_options: [],
            used_by: ["triage_new_mail"],
            message: null,
        }],
        warnings: ["The automation starts paused. Turn it on once the table has rows."],
        has_destructive_steps: steps.some((step) => step.destructive),
    };
}

interface SampleJob {
    id: string;
    podId: string;
    owner: string;
    repo: string;
    startedAt: number;
    appliedAt: number | null;
    /** Set when it was stopped, with how far it had got. */
    cancelled: { status: ImportStatus; done: number } | null;
    plan: ImportPlan | null;
}

/** A job's state now, worked out from when it started and was applied. */
function readJob(job: SampleJob, now: number): ImportStatusResponse {
    const base = {
        import_id: job.id,
        pod_id: job.podId,
        source_kind: "GITHUB",
        events_url: "",
        error: null as string | null,
        error_code: null as string | null,
        retryable: false,
        warnings: [] as string[],
        connect_url: null as string | null,
    };
    const plan = job.plan;
    const total = plan?.steps.length ?? 0;
    const at = (done: number, running: boolean): PlanStep[] => (plan?.steps ?? []).map((step, index) => ({
        ...step,
        status: index < done ? (step.action === "SKIP" ? "SKIPPED" : "DONE") : index === done && running ? "RUNNING" : "PENDING",
    }));
    if (job.cancelled) {
        return { ...base, status: job.cancelled.status, plan: plan ? { ...plan, steps: at(job.cancelled.done, false) } : null, progress: { done: job.cancelled.done, total } };
    }
    const since = now - job.startedAt;
    if (job.appliedAt === null) {
        if (since < QUEUED_MS) return { ...base, status: "QUEUED", plan: null, progress: { done: 0, total: 0 } };
        if (since < FETCHED_MS) return { ...base, status: "FETCHING", plan: null, progress: { done: 0, total: 0 } };
        if (!plan) {
            return {
                ...base,
                status: "FAILED",
                plan: null,
                progress: { done: 0, total: 0 },
                error: `Repository ${job.owner}/${job.repo} was not found or requires a connected GitHub account.`,
                error_code: "GITHUB_REPOSITORY_NOT_FOUND",
            };
        }
        if (since < PLANNED_MS) return { ...base, status: "PLANNING", plan: null, progress: { done: 0, total: 0 } };
        return { ...base, status: "AWAITING_CONFIRMATION", plan, progress: { done: 0, total } };
    }
    const done = Math.min(total, Math.floor((now - job.appliedAt) / STEP_MS));
    if (done >= total) return { ...base, status: "COMPLETED", plan: { ...plan!, steps: at(total, false) }, progress: { done: total, total } };
    return { ...base, status: "APPLYING", plan: { ...plan!, steps: at(done, true) }, progress: { done, total } };
}

export function installSample(): InstallSource {
    const jobs = new Map<string, SampleJob>();
    /* The stand-ins made here: a plan for one is a plan for a new Lemma, with
       nothing in it there already. */
    const createdHere = new Set<string>();
    let made = 0;
    let pending: (PendingInstall & { at: number }) | null = null;

    const held = (importId: string): SampleJob => {
        const job = jobs.get(importId);
        if (!job) throw new Error("That installation is no longer here. Start again.");
        return job;
    };

    return {
        async readme(owner, repo) {
            await wait(300);
            return sampleReadme(owner, repo);
        },

        async createOrg(name) {
            await wait(400);
            made += 1;
            return { id: `sample-org-${made}`, name };
        },

        async createLemma(_orgId, name) {
            await wait(400);
            createdHere.add(SUPPORT.id);
            return { ...SUPPORT, name: name.trim() || SUPPORT.name };
        },

        async startImport(podId, input) {
            await wait(400);
            made += 1;
            const id = `sample-import-${made}`;
            const into = SAMPLE_LEMMAS.some((one) => one.id === podId) && !createdHere.has(podId) ? podId : null;
            const plan = templateOf(input.owner, input.repo) ? samplePlan(input.repo, into) : null;
            const job: SampleJob = { id, podId, owner: input.owner, repo: input.repo, startedAt: Date.now(), appliedAt: null, cancelled: null, plan };
            jobs.set(id, job);
            return readJob(job, Date.now());
        },

        async readImport(_podId, importId) {
            return readJob(held(importId), Date.now());
        },

        async applyImport(_podId, importId, input) {
            await wait(350);
            const job = held(importId);
            const now = readJob(job, Date.now());
            if (now.status !== "AWAITING_CONFIRMATION") throw new Error("This installation is not waiting on a review any more.");
            if (job.plan?.has_destructive_steps && !input.confirmDestructive) throw new Error("Confirm that what is marked Replaces yours may be replaced.");
            const missing = job.plan?.variables.find((one) => one.required && one.kind !== "pod_member" && !input.variables[one.name]);
            if (missing) throw new Error(`The variable ${missing.name} needs a value.`);
            job.appliedAt = Date.now();
            return readJob(job, Date.now());
        },

        async cancelImport(_podId, importId) {
            await wait(250);
            const job = held(importId);
            const now = readJob(job, Date.now());
            if (now.status === "COMPLETED" || now.status === "FAILED") return;
            job.cancelled = { status: now.progress.done > 0 ? "PARTIALLY_CANCELLED" : "CANCELLED", done: now.progress.done };
        },

        async pending() {
            if (!pending || Date.now() - pending.at > 24 * 60 * 60 * 1000) return null;
            return { path: pending.path, title: pending.title };
        },
        async rememberPending(install) {
            pending = { ...install, at: Date.now() };
        },
        async forgetPending() {
            pending = null;
        },
    };
}

