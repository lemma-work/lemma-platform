"use client";

/** What the outcome frame reads, all of it through the existing data source:
 *  no endpoint here is new. The plan is a pod file and its number is a
 *  read-only query, so a plan works on any deployment that can store a file. */

import { useCallback } from "react";
import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { source, NEW_CONVERSATION, type ConversationRef, type Pod, type PodDetail } from "@/data";
import { isMissing } from "@/session/auth-state";
import { configuredApiUrl } from "@/session/origins";
import { NOWHERE, writeAddress } from "@/shell/address";
import { PLAN_PATH, parsePlan, readMeasure, serializePlan, type Plan } from "./model";

/** Each outcome's recent conversations, every agent's, as the viewer can see
 *  them: the first page, newest first, which is what "lately" means here.
 *  Kept under the workspace's `["conversations", podId]`, so a message sent
 *  from a thread in this frame reads the list again, as it does there. */
export function usePodActivity(pods: readonly Pod[]): { byPod: Map<string, ConversationRef[]>; pending: boolean } {
    /* Stable, so the map keeps its identity until a result changes: an
       inline combine runs, and makes a new map, on every render. */
    const combine = useCallback((results: UseQueryResult<ConversationRef[]>[]) => ({
        byPod: new Map(pods.map((pod, index) => [pod.id, results[index]?.data ?? []])),
        pending: results.some((result) => result.isPending),
    }), [pods]);
    return useQueries({
        queries: pods.map((pod) => ({
            queryKey: ["conversations", pod.id, "outcome-activity"],
            queryFn: async () => (await source.listConversationsPage(pod.id, null, undefined, true)).items,
            staleTime: 30_000,
            refetchInterval: 60_000,
        })),
        combine,
    });
}

/** Each pod's answer, by pod id, as one map that changes only when one does. */
function useByPod<T>(pods: readonly Pod[]) {
    return useCallback((results: UseQueryResult<T>[]) => new Map(pods.map((pod, index) => [pod.id, results[index]?.data])), [pods]);
}

/** Who is in each outcome. The pod list carries no roster, so each is asked
 *  for under the key the workspace already uses, and shared with it. */
export function usePodDetails(pods: readonly Pod[]): Map<string, PodDetail | undefined> {
    return useQueries({
        queries: pods.map((pod) => ({
            queryKey: ["pod-detail", pod.id],
            queryFn: () => source.getPodDetail(pod.id, pod.name, pod.iconUrl),
            staleTime: 5 * 60_000,
        })),
        combine: useByPod<PodDetail>(pods),
    });
}

/** The plan file, or null when the pod has none yet. Only a missing file is
 *  "no plan": any other failure is thrown, so the query retries a transient
 *  one and shows the rest as a read that failed, never as a draft to save
 *  over a file that is there. */
export async function readPlan(podId: string): Promise<Plan | null> {
    let file;
    try {
        file = await source.readFile(podId, PLAN_PATH);
    } catch (error) {
        if (isMissing(error)) return null;
        throw error;
    }
    /* There, but not as text: a read that failed, not a pod with no plan. */
    if (typeof file.text !== "string") throw new Error("plan.md could not be read as text");
    return { ...parsePlan(file.text), file: file.text };
}

/** Thrown when plan.md changed between reading it and saving over it. */
export class PlanMovedOn extends Error {
    constructor() {
        super("plan.md changed since it was read. It has been read again; make the change once more.");
        this.name = "PlanMovedOn";
    }
}

/** Saves the plan and answers it as now written. An existing file is read
 *  again first: when it still says what the plan was read from, the plan's
 *  changes are written into it, keeping everything the parser does not
 *  read; when someone -- often the Lemma -- wrote it since, nothing is
 *  saved, so their lines are not lost. */
export async function savePlan(podId: string, plan: Plan, exists: boolean): Promise<Plan> {
    if (!exists) {
        const text = serializePlan(plan);
        await source.createFile(podId, PLAN_PATH, text);
        return { ...plan, file: text };
    }
    const now = await source.readFile(podId, PLAN_PATH);
    const current = typeof now.text === "string" ? now.text : null;
    if (plan.file !== undefined && current !== plan.file) throw new PlanMovedOn();
    const text = serializePlan(plan, current);
    await source.writeFile(podId, PLAN_PATH, text);
    return { ...plan, file: text };
}

export function usePlans(pods: readonly Pod[]): Map<string, Plan | null | undefined> {
    return useQueries({
        queries: pods.map((pod) => ({
            queryKey: ["outcome-plan", pod.id],
            queryFn: () => readPlan(pod.id),
            staleTime: 5 * 60_000,
        })),
        combine: useByPod<Plan | null>(pods),
    });
}

/** The number a plan's measure answers, when it has one. */
export function useMeasure(podId: string, plan: Plan | null | undefined) {
    const sql = plan?.measure ?? null;
    return useQuery({
        queryKey: ["outcome-measure", podId, sql],
        queryFn: async () => readMeasure((await source.runQuery(podId, sql as string)).items),
        enabled: Boolean(sql),
        staleTime: 5 * 60_000,
        retry: false,
    });
}

/** Where a person goes when the frame hands them to the workspace: the
 *  workspace's own grammar, so links stay valid as it changes. */
export const classic = {
    pod: (podId: string) => writeAddress({ ...NOWHERE, podId }),
    conversation: (podId: string, conversationId: string) => writeAddress({ ...NOWHERE, podId, tabId: "conversation", conversationId }),
    run: (podId: string, runId: string) => writeAddress({ ...NOWHERE, podId, tabId: "run:" + runId }),
    table: (podId: string, name: string) => writeAddress({ ...NOWHERE, podId, tabId: "table:" + name }),
    record: (podId: string, table: string, id: string) => writeAddress({ ...NOWHERE, podId, tabId: "record:" + table + ":" + id }),
    app: (podId: string, id: string) => writeAddress({ ...NOWHERE, podId, tabId: "app:" + id }),
    file: (podId: string, path: string) => writeAddress({ ...NOWHERE, podId, tabId: "file:" + path }),
    agent: (podId: string, name: string) => writeAddress({ ...NOWHERE, podId, tabId: "bot:" + name }),
    schedules: (podId: string) => writeAddress({ ...NOWHERE, podId, tabId: "space:about", section: "schedules" }),
    workflow: (podId: string, name: string) => writeAddress({ ...NOWHERE, podId, tabId: "workflow:" + name }),
};

/** The address Claude, ChatGPT or any MCP client connects to for one pod. */
export function mcpUrl(podId: string): string | null {
    const api = configuredApiUrl();
    return api ? `${api.replace(/\/+$/, "")}/mcp/${podId}` : null;
}

/** Ask the pod's own agent, in a new conversation, and say where it is. */
export async function askIn(podId: string, text: string): Promise<string | null> {
    const conversation = await source.send(podId, text, undefined, NEW_CONVERSATION);
    return conversation.id;
}

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
