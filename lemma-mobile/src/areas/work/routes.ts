import { lemmaTab, part, underLemma } from "../paths";

/** What a Lemma runs. Other areas link here (a workflow, a schedule or an
 *  agent in a chat; a run in a Lemma's Recently; a form in Needs you), so
 *  these stay as they are:
 *
 *  - `runs`: a Lemma's page on its Runs tab, on one of its views;
 *  - `plan`: a Lemma's page on its Plan tab;
 *  - `workflow`: `(in)/lemma/[podId]/workflow/[name]`, its steps and runs;
 *  - `run`: `(in)/lemma/[podId]/run/[id]`, one run's step tree;
 *  - `schedule`: `(in)/lemma/[podId]/schedule/[id]`, one schedule and its
 *    firings; `NEW_SCHEDULE` for the form that makes one;
 *  - `agent`: `(in)/lemma/[podId]/agent/[name]`, one agent and its chats. */

export const NEW_SCHEDULE = "new";

/** The Runs tab's views, in the order its chips run. */
export const RUNS_VIEWS = ["workflows", "waiting", "running", "history", "schedules", "agents", "xray"] as const;
export type RunsView = (typeof RUNS_VIEWS)[number];

export const workRoutes = {
    runs: (podId: string, view?: RunsView) => lemmaTab(podId, "runs") + (view ? `?view=${part(view)}` : ""),
    plan: (podId: string) => lemmaTab(podId, "plan"),
    workflow: (podId: string, name: string) => underLemma(podId, "workflow", name),
    run: (podId: string, id: string) => underLemma(podId, "run", id),
    schedule: (podId: string, id: string) => underLemma(podId, "schedule", id),
    agent: (podId: string, name: string) => underLemma(podId, "agent", name),
};
