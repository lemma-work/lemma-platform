import { parseSSEJson, readSSE, WorkflowRunStatus } from "lemma-sdk";
import { readText } from "../../data/io";
import { isPodDefaultAgent } from "@web/data/agent-names";
import type { LiveContext } from "../context";
import { filesLive } from "../files/live";
import { PLAN_PATH } from "./web/plan";
import { agentChanges, agentRows, readAgentDetail } from "@web/data/agents";
import { readPodConnectors } from "@web/data/pod-connectors";
import { readAssignments, readRun, readRunDetail, readRuns, readWorkflows, type RunRow } from "@web/workflow/runs";
import { readRuntime, type Runtime } from "@web/data/runtimes";
import { readRun as readScheduleRun, readRuns as readScheduleRuns, readSchedule, readSchedules, readServerEvents } from "@web/schedule/schedules";
import type { AgentChat, Member, Surface, WorkSource } from "./source";

/** What a Lemma runs, from the server: the calls the web makes
 *  (`lemma-frontend/src/workflow/use-run.ts`, `data/live.ts`'s agents and
 *  standing work, `outcomes/data.ts`'s plan), read by the web's readers. */

type Raw = Record<string, unknown>;

const record = (value: unknown): Raw => (value && typeof value === "object" && !Array.isArray(value) ? (value as Raw) : {});
const itemsOf = (value: unknown): Raw[] => {
    if (Array.isArray(value)) return value.map(record);
    const items = record(value).items;
    return Array.isArray(items) ? items.map(record) : [];
};
const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/** 200 runs a page; ten pages is two thousand runs in flight on one workflow
 *  (the web's `MAX_BOARD_PAGES`). */
const BOARD_PAGES = 10;

export function workLive(context: LiveContext): WorkSource {
    const pod = (podId: string) => context.client(podId);
    const files = filesLive(context);

    /* The Lemma's own agent answers under the Lemma's name, as the web's
       `answeringAs` has it; the page that lists it knows that name. */
    return {
        async workflows(podId) {
            return readWorkflows(await pod(podId).workflows.list({ limit: 100 }));
        },

        async workflow(podId, name) {
            return await pod(podId).workflows.get(name);
        },

        async recentRuns(podId, limit) {
            return readRuns(await pod(podId).request("GET", `/pods/${podId}/workflow-runs`, { params: { limit } }));
        },

        async workflowRuns(podId, name, page) {
            const listed = await pod(podId).workflows.runs.list(name, { limit: 20, pageToken: page });
            return { items: readRuns(listed), next: text(record(listed).next_page_token) || null };
        },

        async runsInFlight(podId, workflowId) {
            const runs: RunRow[] = [];
            let pageToken: string | undefined;
            for (let page = 0; page < BOARD_PAGES; page += 1) {
                const listed = await pod(podId).workflows.runs.listInPod({
                    status: [WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING, WorkflowRunStatus.WAITING],
                    workflowId,
                    limit: 200,
                    pageToken,
                });
                runs.push(...readRuns(listed));
                pageToken = text(record(listed).next_page_token) || undefined;
                if (!pageToken) break;
            }
            return runs;
        },

        async waitingOnMe(podId) {
            return readAssignments(await pod(podId).workflows.runs.waitingAssignedToMe({ limit: 100 }));
        },

        async run(podId, runId) {
            return readRunDetail(await pod(podId).workflows.runs.get(runId, podId));
        },

        /* The server pushes the whole run on every change; the first frame is
           the run as it stands, so opening the stream again is the catch-up. */
        async watchRun(podId, runId, onRun, signal) {
            const stream = await pod(podId).stream(`/pods/${podId}/workflow-runs/${runId}/stream`, { signal });
            for await (const frame of readSSE(stream)) {
                if (signal.aborted) return;
                const parsed = parseSSEJson<{ type?: string; data?: unknown }>(frame);
                const detail = readRunDetail(parsed?.data);
                if (detail) onRun(detail);
                if (parsed?.type === "completed") return;
            }
        },

        async startRun(podId, name) {
            return readRun(await pod(podId).workflows.runs.create(name));
        },

        async cancelRun(podId, runId) {
            await pod(podId).workflows.runs.cancel(runId, podId);
        },

        async retryRun(podId, runId) {
            await pod(podId).workflows.runs.retry(runId, podId);
        },

        async submitForm(podId, runId, nodeId, inputs) {
            /* `node_id` is the wait's own: the server refuses a mismatch with a
               422, so a form can never be sent to a step the run has passed. */
            await pod(podId).workflows.runs.submitForm(runId, { node_id: nodeId, inputs }, podId);
        },

        async schedules(podId) {
            /* A hundred: this is the list itself, and a Lemma that keeps
               thirty schedules would otherwise silently show eighteen. */
            return readSchedules(await pod(podId).schedules.list({ limit: 100 }));
        },

        async scheduleRuns(podId, scheduleId, skipped) {
            return readScheduleRuns(await pod(podId).request("GET", `/pods/${podId}/schedules/${scheduleId}/runs`, { params: { limit: 20, skipped } }));
        },

        async setScheduleActive(podId, scheduleId, active) {
            /* The saved schedule, read: resuming one the breaker stopped clears
               its failure count on the server. */
            return readSchedule(await pod(podId).schedules.update(scheduleId, { is_active: active }));
        },

        async setScheduleVisibility(podId, scheduleId, visibility) {
            await pod(podId).schedules.update(scheduleId, { visibility } as never);
        },

        async retryScheduleRun(podId, scheduleId, runId) {
            return readScheduleRun(await pod(podId).request("POST", `/pods/${podId}/schedules/${scheduleId}/runs/${runId}/retry`));
        },

        async createSchedule(podId, body) {
            await pod(podId).request("POST", `/pods/${podId}/schedules`, { body });
        },

        async deleteSchedule(podId, scheduleId) {
            await pod(podId).schedules.delete(scheduleId);
        },

        async serverEvents(podId) {
            return readServerEvents(await pod(podId).request("GET", `/pods/${podId}/events`));
        },

        async podConnectors(podId) {
            return readPodConnectors(await pod(podId).connectors.pod.list());
        },

        async agents(podId) {
            return agentRows(await pod(podId).agents.list({ limit: 100 }));
        },

        async agent(podId, name) {
            return readAgentDetail(await pod(podId).agents.get(name));
        },

        async updateAgent(podId, name, before, after) {
            /* The PATCH answers no grants, so the saved agent is read again
               rather than taken from the reply. */
            await pod(podId).agents.update(name, agentChanges(before, after) as never);
            return readAgentDetail(await pod(podId).agents.get(name));
        },

        async deleteAgent(podId, name) {
            await pod(podId).agents.delete(name);
        },

        async agentChats(podId, agent) {
            const listed = agent.front
                ? await pod(podId).conversations.listDefault({ pod_id: podId, limit: 30 })
                : await pod(podId).conversations.listByAgent(agent.name, { pod_id: podId, limit: 30 });
            return itemsOf(listed).flatMap((row): AgentChat[] => {
                const id = text(row.id);
                if (!id) return [];
                return [{ id, title: text(row.title) || "Untitled", at: text(row.last_activity_at) || text(row.updated_at) || null }];
            });
        },

        async talkTo(podId, agentName, words) {
            const client = pod(podId);
            const made = agentName && !isPodDefaultAgent(agentName)
                ? await client.conversations.createForAgent(agentName, { pod_id: podId })
                : await client.conversations.create({ pod_id: podId });
            await client.conversations.appendMessage(made.id, { content: words }, { pod_id: podId });
            return made.id;
        },

        async runtimes(orgId) {
            /* Retired ones too: an agent pinned to one is said to be. */
            const listed = await context.client().agentRuntime.listProfiles(orgId, { includeDisabled: true });
            return itemsOf(listed).map(readRuntime).filter((one): one is Runtime => one !== null);
        },

        async runtime(orgId, runtimeId) {
            return readRuntime(await context.client().agentRuntime.getProfile(orgId, runtimeId));
        },

        async surfaces(podId) {
            const listed = await pod(podId).podSurfaces.list(podId, { limit: 30 });
            return itemsOf(listed).flatMap((surface): Surface[] => {
                const id = text(surface.id);
                const platform = text(surface.platform);
                if (!id || !platform) return [];
                const reach = record(surface.reach);
                const agent = text(surface.agent_name) || null;
                const status = text(surface.status) || "ACTIVE";
                return [{
                    id,
                    platform,
                    handle: text(reach.handle) || text(surface.surface_identity_username) || text(surface.surface_identity_email),
                    active: status === "ACTIVE",
                    status,
                    mine: surface.uses_default_agent === true || isPodDefaultAgent(agent),
                    agentKey: agent,
                }];
            });
        },

        async members(podId) {
            const listed = await pod(podId).podMembers.list(podId, { limit: 200 });
            return itemsOf(listed).flatMap((member): Member[] => {
                const id = text(member.id);
                if (!id) return [];
                const name = text(member.user_name) || text(member.user_email) || text(member.email) || "Someone here";
                return [{ id, userId: text(member.user_id) || null, name }];
            });
        },

        async planText(podId) {
            return readText(context.server, podId, PLAN_PATH);
        },

        async writePlan(podId, body, exists) {
            if (exists) await files.writeText(podId, PLAN_PATH, body);
            else await files.createText(podId, PLAN_PATH, body);
        },

        async query(podId, sql) {
            return itemsOf(await pod(podId).datastore.query(sql));
        },

        mcpUrl(podId) {
            return `${context.server.api.replace(/\/+$/, "")}/mcp/${podId}`;
        },
    };
}

