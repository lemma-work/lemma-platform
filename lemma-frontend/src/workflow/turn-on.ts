/** Whether a workflow runs by itself, and for whom — and what it takes to
 *  make it do so for you.
 *
 *  A workflow's `start` is a template: a time, an app event, a table change.
 *  Nothing fires until a *schedule* points at it (`domain/start.py`), and a
 *  schedule always runs as the person who made it — the one exception being a
 *  table-change schedule on a table with row security, which runs as each
 *  row's owner (`datastore_event_handler.py`). There is no "install for me"
 *  on the server; getting a workflow for yourself *is* making your own
 *  schedule for it, with your own connected account where it listens to an
 *  app. That is what "Turn on for me" does.
 *
 *  Personal schedules are invisible to everyone but their owner, so a list of
 *  who else has turned something on can only ever be partial — this never
 *  claims more than it can see.
 */

import { isRecord, str } from "./runs";
import type { StandingJob } from "@/schedule/schedules";

export type Automation =
    | { kind: "manual" }
    | { kind: "time" }
    | { kind: "event"; connectorId: string; triggerId: string }
    | { kind: "rows"; table: string; operations: string[] };

export function automationOf(start: unknown): Automation {
    if (!isRecord(start)) return { kind: "manual" };
    const config = isRecord(start.config) ? start.config : {};
    switch ((str(start.type) ?? "").toUpperCase()) {
        case "SCHEDULED": return { kind: "time" };
        case "EVENT": return { kind: "event", connectorId: str(config.connector_id) ?? "", triggerId: str(config.connector_trigger_id) ?? "" };
        case "DATASTORE_EVENT": return {
            kind: "rows",
            table: str(config.table_name) ?? "",
            operations: Array.isArray(config.operations) ? config.operations.filter((one): one is string => typeof one === "string") : [],
        };
        default: return { kind: "manual" };
    }
}

export type TurnOn =
    /** Started by hand; there is nothing to turn on. */
    | { status: "manual" }
    /** Running for you (or, space-wide, for everyone), by this schedule. */
    | { status: "on"; job: StandingJob; byYou: boolean }
    /** Set up, but paused or broken. */
    | { status: "stopped"; job: StandingJob; byYou: boolean }
    /** Nothing makes it run. `forYou` says whether turning it on is a
     *  personal act (each person) or a space-wide one (admin). */
    | { status: "off"; forYou: boolean };

/** The schedules that point at this workflow, as far as the caller can see. */
export function schedulesFor(jobs: StandingJob[], workflow: string): StandingJob[] {
    return jobs.filter((job) => job.target.kind === "workflow" && job.target.name === workflow && !job.broken);
}

/** Where this workflow stands for the person looking.
 *
 *  Space-wide (GLOBAL, or a table change, which runs as each row's owner
 *  anyway): any live schedule is the answer. Per person (USER): only a
 *  schedule of your own is. */
export function turnOnOf(automation: Automation, perPerson: boolean, jobs: StandingJob[], me: string | null): TurnOn {
    if (automation.kind === "manual") return { status: "manual" };
    const personal = perPerson && automation.kind !== "rows";
    const relevant = personal ? jobs.filter((job) => me !== null && job.ownerId === me) : jobs;
    const live = relevant.find((job) => job.active && !job.needsSetup);
    if (live) return { status: "on", job: live, byYou: me !== null && live.ownerId === me };
    const stopped = relevant[0];
    if (stopped) return { status: "stopped", job: stopped, byYou: me !== null && stopped.ownerId === me };
    return { status: "off", forYou: personal };
}

/** The schedule to create. Per person, it is PERSONAL — yours, running as
 *  you; space-wide it is POD, so the space can see it is on. A webhook takes
 *  its trigger from the workflow's own start and only needs your account. */
export function turnOnRequest(workflow: string, automation: Automation, perPerson: boolean, choice: { cron?: string; timezone?: string; accountId?: string }): Record<string, unknown> | null {
    const body: Record<string, unknown> = {
        workflow_name: workflow,
        visibility: perPerson && automation.kind !== "rows" ? "PERSONAL" : "POD",
    };
    switch (automation.kind) {
        case "time":
            if (!choice.cron) return null;
            body.schedule_type = "TIME";
            body.config = choice.timezone ? { cron: choice.cron, timezone: choice.timezone } : { cron: choice.cron };
            return body;
        case "event":
            if (!choice.accountId) return null;
            body.schedule_type = "WEBHOOK";
            body.config = {};
            body.account_id = choice.accountId;
            return body;
        case "rows":
            body.schedule_type = "DATASTORE";
            body.config = { table_name: automation.table, operations: automation.operations.length ? automation.operations : ["INSERT"] };
            return body;
        default:
            return null;
    }
}

/** Who a schedule runs for, as a sentence. */
export function runsForOf(automation: Automation, perPerson: boolean): string {
    if (automation.kind === "manual") return "Runs when someone starts it, as that person.";
    if (automation.kind === "rows") return "Runs as whoever owns the row that changed.";
    return perPerson
        ? "Each person turns it on for themselves; it runs as them, with their own accounts."
        : "One switch for the whole space; it runs as whoever turned it on.";
}
