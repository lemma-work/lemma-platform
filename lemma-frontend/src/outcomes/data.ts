"use client";

/** What the outcome frame reads, all of it through the data source. What a
 *  Lemma owns and its number are kept on the Lemma; the plan's prose is a
 *  pod file. A deployment that keeps nothing on the Lemma still works from
 *  the file alone, its front matter and a read-only query. */

import { useCallback } from "react";
import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { source, NEW_CONVERSATION, type ConversationRef, type Pod, type PodDetail } from "@/data";
import { isMissing } from "@/session/auth-state";
import { configuredApiUrl } from "@/session/origins";
import { PLAN_PATH, ownedInput, parsePlan, readMeasure, serializePlan, withOwned, withoutOwned, type Plan } from "./model";

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
    const [file, owned] = await Promise.all([readPlanFile(podId), source.getOwned(podId)]);
    return withOwned(file, owned);
}

async function readPlanFile(podId: string): Promise<Plan | null> {
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
    /* What it owns goes to the Lemma first, which reads a new measure as the
       person saving and refuses one they cannot read; the file then keeps
       only the prose. An API with nowhere to keep it answers null, and the
       file keeps all of it in its front matter, as it always did. */
    const input = ownedInput(plan);
    const owned = input ? await source.setOwned(podId, input) : null;
    const written = owned || plan.owned ? withoutOwned(plan) : plan;
    let text: string;
    if (!exists) {
        text = serializePlan(written);
        await source.createFile(podId, PLAN_PATH, text);
    } else {
        const now = await source.readFile(podId, PLAN_PATH);
        const current = typeof now.text === "string" ? now.text : null;
        if (plan.file !== undefined && current !== plan.file) throw new PlanMovedOn();
        text = serializePlan(written, current);
        await source.writeFile(podId, PLAN_PATH, text);
    }
    return withOwned({ ...parsePlan(text), file: text }, owned) ?? { ...plan, file: text };
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

/** The number a plan's measure answers, when it has one. A Lemma that keeps
 *  what it owns has already read it, for everyone alike; only a plan from
 *  front matter is run here, as the viewer, which is why two people could
 *  see two numbers for it. */
export function useMeasure(podId: string, plan: Plan | null | undefined) {
    const sql = plan?.owned ? null : (plan?.measure ?? null);
    const query = useQuery({
        queryKey: ["outcome-measure", podId, sql],
        queryFn: async () => readMeasure((await source.runQuery(podId, sql as string)).items),
        enabled: Boolean(sql),
        staleTime: 5 * 60_000,
        retry: false,
    });
    if (!plan?.owned) return { data: query.data, isError: query.isError };
    return { data: plan.value ?? undefined, isError: Boolean(plan.measure) && plan.value === null && Boolean(plan.problem) };
}

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

/** What "Write it with {name}" sends. What it owns is kept with
 *  `set_what_you_own`, on the Lemma; the file holds the plan's prose, in the
 *  sections this frame reads back line for line. */
export function planRequest(outcome: string): string {
    return [
        outcome
            ? `Set what you own with set_what_you_own: "${outcome}", as a bar held every week or a finish line by a date, with its target, and a measure once what it counts exists.`
            : "Work out with me what you should own, a bar held every week or a finish line by a date, and keep it with set_what_you_own.",
        `Then write the plan in ${PLAN_PATH} under ## What must be true, ## How we work, ## Who holds what and ## Never: one line per bullet, each ending with who said it and when. No front matter.`,
        "Only write lines that name something that exists here or that you are about to set up, and say what you built.",
    ].join(" ");
}

/** The setup chat's first message for a Lemma started here, in the same
 *  words a Lemma started on the server gets, so both set up the same way. */
export function setupRequest(outcome: string): string {
    const sentence = outcome.trim();
    const stop = /[.!?]$/.test(sentence) ? "" : ".";
    return `Set yourself up to own this: "${sentence}${stop}" Nothing is built yet, so build what holds the work and set how it is counted with set_what_you_own. Use the lemma-setup skill: ask me where this work happens, bring in the last few weeks of it, and keep it coming in. Ask me whatever you need.`;
}
