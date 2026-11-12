"use client";

/** What the outcome frame reads. Most of it goes through the existing data
 *  source; what a Lemma owns, its number, the templates a new one starts from
 *  and what waits on you across the organization come from the server's own
 *  reads of them, on a live session only. The plan's lines are a pod file. */

import { useCallback, useEffect, useState } from "react";
import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { OutcomeKind, type ImportStatusResponse, type NeedsYouItemResponse, type OutcomeStandingResponse, type OutcomeTemplateCard, type StartLemmaRequest } from "lemma-sdk";
import { source, NEW_CONVERSATION, type ConversationRef, type Pod, type PodDetail } from "@/data";
import { characterUrl, CHARACTERS, type CharacterName } from "@/shell/character";
import { isMissing } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { configuredApiUrl } from "@/session/origins";
import { CONVERSATION_ASKS, ASKS_REFETCH_MS } from "@/thread/waiting-on-you";
import { PLAN_PATH, parsePlan, readMeasure, serializePlan, type NeedsItem, type Owned, type Plan, type ShelfCard } from "./model";

const live = () => source.label === "live";

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

/** The number a plan's measure answers, when it has one: the server's
 *  reading when the Lemma keeps what it owns, else -- for a Lemma from
 *  before it did, whose plan file carries the measure -- the query run here. */
export function useMeasure(podId: string, plan: Plan | null | undefined) {
    const sql = plan?.owned ? null : plan?.measure ?? null;
    const asked = useQuery({
        queryKey: ["outcome-measure", podId, sql],
        queryFn: async () => readMeasure((await source.runQuery(podId, sql as string)).items),
        enabled: Boolean(sql),
        staleTime: 5 * 60_000,
        retry: false,
    });
    if (plan?.owned) return { data: plan.owned.value, isError: Boolean(plan.owned.problem) };
    return { data: asked.data, isError: asked.isError };
}

/** What a standing read says, in the frame's names; null when the Lemma owns
 *  nothing on the server. */
export function ownedOf(standing: OutcomeStandingResponse | undefined): Owned | null {
    const outcome = standing?.outcome;
    if (!outcome) return null;
    return {
        sentence: outcome.sentence,
        kind: outcome.kind === "date" ? "date" : "week",
        target: outcome.target ?? null,
        unit: outcome.unit ?? null,
        due: outcome.due ?? null,
        measure: outcome.measure ?? null,
        value: standing?.value ?? null,
        problem: standing?.problem ?? null,
    };
}

export const standingKey = (podId: string) => ["outcome-standing", podId];

/** What each Lemma owns and where it stands, as the server keeps it: one
 *  number, the same for every member. */
export function useOwned(pods: readonly Pod[]): Map<string, Owned | null | undefined> {
    return useQueries({
        queries: pods.map((pod) => ({
            queryKey: standingKey(pod.id),
            queryFn: async () => ownedOf(await lemma(pod.id).pods.outcome.standing(pod.id)),
            enabled: live(),
            staleTime: 60_000,
            retry: false,
        })),
        combine: useByPod<Owned | null>(pods),
    });
}

/** The Lemma's last weeks against what it owns, oldest first. */
export function useWeeks(podId: string, enabled: boolean) {
    return useQuery({
        queryKey: ["outcome-weeks", podId],
        queryFn: async () => (await lemma(podId).pods.outcome.history(podId, 8)).weeks,
        enabled: enabled && live(),
        staleTime: 5 * 60_000,
        retry: false,
    });
}

/** A bar every week or a finish line by a date, as the server names it. */
export function kindOf(kind: "week" | "date"): OutcomeKind {
    return kind === "date" ? OutcomeKind.DATE : OutcomeKind.WEEK;
}

/** Set what a Lemma owns on the server. */
export async function saveOwned(podId: string, owned: Pick<Owned, "sentence" | "kind" | "target" | "unit" | "due" | "measure">): Promise<void> {
    await lemma(podId).pods.outcome.set(podId, {
        sentence: owned.sentence,
        kind: kindOf(owned.kind),
        target: owned.target,
        unit: owned.unit,
        due: owned.kind === "date" ? owned.due : null,
        measure: owned.measure,
    });
}

/* ── starting a Lemma ─────────────────────────────────────────────────── */

function shelfCardOf(card: OutcomeTemplateCard): ShelfCard {
    return {
        id: card.id,
        name: card.name,
        sentence: card.sentence,
        kind: card.kind === "date" ? "date" : "week",
        work: card.function ?? null,
        face: card.face ?? null,
        place: card.place ?? null,
        brings: card.brings ?? [],
        never: card.plan?.never ?? [],
    };
}

/** The templates a new Lemma can start from, as the server ships them. */
export function useShelf() {
    return useQuery({
        queryKey: ["lemma-templates"],
        queryFn: async () => (await lemma().pods.templates()).map(shelfCardOf),
        enabled: live(),
        staleTime: 30 * 60_000,
    });
}

const SETUP_KEY = (podId: string) => `lemma:setup:${podId}`;

/** Start a Lemma, then wear the template's face. Answers where to land: its
 *  setup chat. The import that brings a template's tables and automations is
 *  remembered for the tab, so the chat can say how far it has got. */
export async function startLemma(request: StartLemmaRequest, face: string | null): Promise<{ podId: string; conversationId: string }> {
    const started = await lemma().pods.start(request);
    const podId = started.pod.id;
    const known = CHARACTERS.find((name) => name === face) as CharacterName | undefined;
    if (known) await lemma(podId).pods.update(podId, { icon_url: characterUrl(known) }).catch(() => undefined);
    if (started.import_id) {
        try { sessionStorage.setItem(SETUP_KEY(podId), started.import_id); } catch { /* the chat still opens; it just says nothing of the import */ }
    }
    return { podId, conversationId: started.conversation_id };
}

const SETTLED = new Set(["COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_CANCELLED"]);

/** How far a just-started Lemma's template has got, while it is coming in:
 *  null once it is in, or when this tab did not start it. */
export function useSetup(podId: string): ImportStatusResponse | null {
    const [importId, setImportId] = useState<string | null>(null);
    useEffect(() => {
        try { setImportId(sessionStorage.getItem(SETUP_KEY(podId))); } catch { setImportId(null); }
    }, [podId]);
    const status = useQuery({
        queryKey: ["lemma-setup", podId, importId],
        queryFn: () => lemma(podId).request<ImportStatusResponse>("GET", `/pods/${encodeURIComponent(podId)}/bundle/imports/${encodeURIComponent(importId as string)}`),
        enabled: Boolean(importId) && live(),
        /* Stops once the import settles, and on a read that failed: a gone
           import answers the same every time, and the strip hides on it. */
        refetchInterval: (query) => (query.state.status === "error" || (query.state.data && SETTLED.has(query.state.data.status)) ? false : 1500),
        retry: false,
    });
    const settled = status.data && SETTLED.has(status.data.status);
    useEffect(() => {
        if (settled && status.data?.status === "COMPLETED") {
            try { sessionStorage.removeItem(SETUP_KEY(podId)); } catch { /* nothing kept to forget */ }
        }
    }, [settled, status.data?.status, podId]);
    if (!importId || status.isError) return null;
    return status.data ?? null;
}

/* ── what waits on you, across the organization ───────────────────────── */

function needsItemOf(item: NeedsYouItemResponse): NeedsItem {
    return {
        key: item.key,
        kind: item.kind as NeedsItem["kind"],
        podId: item.pod_id,
        podName: item.pod_name,
        title: item.title ?? null,
        detail: item.detail ?? null,
        since: item.since,
        conversationId: item.conversation_id ?? null,
        workflowRunId: item.workflow_run_id ?? null,
        channelId: item.channel_id ?? null,
        messageId: item.message_id ?? null,
        choices: item.choices ?? [],
    };
}

/** Everything waiting on you in one organization, in one read. Kept under the
 *  asks' key, so whatever re-reads the asks -- an answer given in a chat, a
 *  conversation that starts waiting -- re-reads this too. */
export function useNeedsYou(orgId: string | null) {
    return useQuery({
        queryKey: [CONVERSATION_ASKS, "organization", orgId],
        queryFn: async () => (await lemma().organizations.needsYou(orgId as string)).items.map(needsItemOf),
        enabled: Boolean(orgId) && live(),
        staleTime: ASKS_REFETCH_MS,
        refetchInterval: ASKS_REFETCH_MS,
    });
}

/** Answer a question a Lemma asked in your chat with it. */
export async function answerLine(podId: string, line: { channelId: string; messageId: string }, answer: { choice: string } | { text: string }): Promise<void> {
    await lemma(podId).channels.answer(podId, line.channelId, line.messageId, answer);
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
