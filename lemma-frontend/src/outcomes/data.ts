"use client";

/** What the outcome frame reads. Most of it goes through the existing data
 *  source; what a Lemma owns, its number, the templates a new one starts from
 *  and what waits on you across the organization come from the server's own
 *  reads of them, on a live session only. The plan's lines are a pod file. */

import { useCallback } from "react";
import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { readerZone } from "@/data/time-zone";
import { OutcomeKind, type ImportStatusResponse, type NeedsYouItemResponse, type OutcomeStandingResponse, type OutcomeTemplateCard } from "lemma-sdk";
import { source, NEW_CONVERSATION, type ConversationRef, type Pod, type PodDetail } from "@/data";
import { characterUrl, CHARACTERS, type CharacterName } from "@/shell/character";
import { isMissing } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { configuredApiUrl } from "@/session/origins";
import { CONVERSATION_ASKS, ASKS_REFETCH_MS } from "@/thread/waiting-on-you";
import { joinWire, type JoinPolicy } from "@/data/joining";
import { PLAN_PATH, broughtSaid, parsePlan, readMeasure, serializePlan, type NeedsItem, type Owned, type Plan, type ShelfCard } from "./model";

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

/** Where an import stops: landed, or not landing. */
const SETTLED = new Set(["COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_CANCELLED"]);

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

/* ── a Lemma as a space ────────────────────────────────────────────────── */

/** Who can find a Lemma: everyone in its organization, or only the people
 *  added to it. ("Anyone with a Lemma account" stays in its settings: it
 *  needs the organization opened first, which is nothing to ask at birth.) */
export type LemmaReach = Extract<JoinPolicy, "org" | "invited">;

export interface NewLemma {
    orgId: string;
    name: string;
    /** What it is for, in a line; its description. */
    purpose: string;
    face: CharacterName;
    reach: LemmaReach;
    /** People to add, by their organization membership id. */
    people: readonly string[];
    /** Everyone who joins the organization later joins this Lemma too: the
     *  organization's own, made with it, the way a chat app has a channel
     *  everyone is in. */
    everyone?: boolean;
}

/** The role someone added at birth gets: the one "Add people" offers first. */
const ADDED_ROLE = "POD_EDITOR";

/** A new pod's config as the SDK types it: its join policy is a generated
 *  enum the SDK does not re-export, so the type is read off the method. */
type NewPodConfig = NonNullable<Parameters<ReturnType<typeof lemma>["pods"]["create"]>[0]["config"]>;

/** Make a Lemma, open to whom it should be, and add its people. Answers its
 *  id, and how many people could not be added -- a Lemma that exists is not
 *  undone because one invitation failed; it says so instead. */
export async function createLemma(wanted: NewLemma): Promise<{ podId: string; missed: number }> {
    const purpose = wanted.purpose.trim();
    const pod = await lemma().pods.create({
        organization_id: wanted.orgId,
        name: wanted.name.trim(),
        ...(purpose ? { description: purpose } : {}),
        icon_url: characterUrl(wanted.face),
        config: {
            join_policy: joinWire(wanted.reach) as NewPodConfig["join_policy"],
            ...(wanted.everyone ? { auto_join_new_members: true } : {}),
        },
    });
    let missed = 0;
    for (const memberId of wanted.people) {
        try {
            await lemma(pod.id).podMembers.add(pod.id, { organization_member_id: memberId, roles: [ADDED_ROLE] });
        } catch {
            missed += 1;
        }
    }
    return { podId: pod.id, missed };
}

/** A face for a new Lemma that no other one here wears yet, while there are
 *  any left. */
export function freshFace(worn: readonly (string | null)[]): CharacterName {
    const taken = new Set(worn.filter((url): url is string => Boolean(url)));
    return CHARACTERS.find((name) => !taken.has(characterUrl(name))) ?? CHARACTERS[worn.length % CHARACTERS.length];
}

/** Who can find a Lemma, and whether the organization's newcomers join it on
 *  their own -- which marks the one made with the organization. */
export interface LemmaOpenness {
    reach: JoinPolicy;
    everyone: boolean;
}

/** What a Lemma's openness reads as: null until it is known, and `settled`
 *  once there is nothing more to wait for -- answered, failed, or not asked
 *  at all (the sample workspace), so nothing waits on it for ever. */
export function useLemmaOpenness(podId: string): { openness: LemmaOpenness | null; settled: boolean } {
    /* Who can find it under the key "Who can join" keeps it by, so changing
       it there changes what the Lemma's header says at once. */
    const reach = useQuery({ queryKey: ["pod-join", podId], queryFn: () => source.getPodJoin(podId), enabled: live(), staleTime: 5 * 60_000, retry: false });
    /* Under the same key's prefix, so "Who can join" refreshes it too: closing
       a Lemma also stops it taking in newcomers. */
    const everyone = useQuery({
        queryKey: ["pod-join", podId, "everyone"],
        queryFn: async () => {
            const pod = await lemma(podId).pods.get(podId);
            return ((pod.config ?? {}) as { auto_join_new_members?: unknown }).auto_join_new_members === true;
        },
        enabled: live(),
        staleTime: 5 * 60_000,
        retry: false,
    });
    const openness = reach.data && everyone.data !== undefined ? { reach: reach.data, everyone: everyone.data } : null;
    return { openness, settled: !live() || (!reach.isPending && !everyone.isPending) };
}

/** A Lemma the reader is not in but may join, as the organization lists it. */
export interface JoinableLemma {
    id: string;
    name: string;
    description: string | null;
    iconUrl: string | null;
    people: number;
}

/** How far back History looks. */
export const HISTORY_DAYS = 14;
/** The most chats History asks for from each Lemma: the server's own cap. */
const HISTORY_CHATS = 25;

/** How long the overview may take before it counts as not having answered.
 *  The sidebar is drawn from it, so it must never just keep waiting: past
 *  this, the frame reads each Lemma on its own, as it used to. */
const RECENTS_PATIENCE_MS = 12_000;

/** What moved lately in every Lemma the reader may open: their chats, the
 *  schedules and workflows that ran, and a count per day. One read for the
 *  organization, which the sidebar, Home, the board of Lemmas and History
 *  all draw from. */
export function useRecents(orgId: string | null) {
    return useQuery({
        queryKey: ["org-recents", orgId, HISTORY_DAYS],
        queryFn: () => new Promise<Awaited<ReturnType<ReturnType<typeof lemma>["organizations"]["recents"]>>>((resolve, reject) => {
            const late = setTimeout(() => reject(new Error("It took too long to answer.")), RECENTS_PATIENCE_MS);
            lemma().organizations.recents(orgId as string, { days: HISTORY_DAYS, chats: HISTORY_CHATS, timezone: readerZone() })
                .then(resolve, reject)
                .finally(() => clearTimeout(late));
        }),
        enabled: Boolean(orgId) && live(),
        staleTime: 30_000,
        refetchInterval: 60_000,
        /* One read that either answers or does not: asking three more times
           with a wait between each kept the page saying it was reading for
           as long as a failure took to repeat. */
        retry: false,
    });
}

/** The Lemmas open to everyone in the organization that the reader is not in
 *  yet: what "Browse" lists, as a chat app lists the channels you could join. */
export function useJoinable(orgId: string | null) {
    return useQuery({
        queryKey: ["lemmas-joinable", orgId],
        queryFn: async (): Promise<JoinableLemma[]> => {
            const said = await lemma().pods.listJoinable(orgId as string);
            return said.items.map((row) => ({
                id: row.id,
                name: row.name,
                description: row.description ?? null,
                iconUrl: row.icon_url ?? null,
                people: row.member_count,
            }));
        },
        enabled: Boolean(orgId) && live(),
        staleTime: 60_000,
    });
}

/** Join a Lemma open to the reader. */
export async function joinLemma(podId: string): Promise<void> {
    await lemma().pods.join(podId);
}

/** How long adding a template may take before the chat about it starts
 *  anyway: the Lemma can look for itself once it is asked. */
const TEMPLATE_PATIENCE_MS = 90_000;
const TEMPLATE_POLL_MS = 1_500;

function wait(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function importStatus(podId: string, importId: string): Promise<ImportStatusResponse> {
    return lemma(podId).request<ImportStatusResponse>("GET", `/pods/${encodeURIComponent(podId)}/bundle/imports/${encodeURIComponent(importId)}`);
}

/** Bring a template's tables, skills and automations into a Lemma that
 *  already exists: plan it, apply the plan as it stands, and wait for it to
 *  land, saying how far it has got. Answers whether it landed; a failure the
 *  import names is thrown with its words. */
export async function addTemplate(podId: string, template: string, progress: (said: string) => void): Promise<boolean> {
    const base = `/pods/${encodeURIComponent(podId)}/bundle/imports`;
    const started = await lemma(podId).request<ImportStatusResponse>("POST", base, { body: { kind: "TEMPLATE", template } });
    const until = Date.now() + TEMPLATE_PATIENCE_MS;
    let state = started;
    while (state.status !== "AWAITING_CONFIRMATION") {
        if (SETTLED.has(state.status)) throw new Error(state.error || "That template couldn’t be added.");
        if (Date.now() > until) return false;
        await wait(TEMPLATE_POLL_MS);
        state = await importStatus(podId, started.import_id);
    }
    const brought = broughtSaid(state.plan?.steps ?? []);
    state = await lemma(podId).request<ImportStatusResponse>("POST", `${base}/${encodeURIComponent(started.import_id)}/apply`, { body: {} });
    while (!SETTLED.has(state.status)) {
        const done = state.progress?.done ?? 0;
        const total = state.progress?.total ?? 0;
        progress(`Adding ${brought || "it"}${total ? ` · ${done} of ${total}` : ""}…`);
        if (Date.now() > until) return false;
        await wait(TEMPLATE_POLL_MS);
        state = await importStatus(podId, started.import_id);
    }
    if (state.status !== "COMPLETED") throw new Error(state.error || "That template couldn’t be added.");
    return true;
}

/** What "Write it with {name}" sends, for a Lemma that owns nothing yet.
 *  `purpose` is what it is for, its description, which is not yet a result
 *  it owns. What it owns is kept with `set_what_you_own`, on the Lemma; the
 *  file holds the plan's prose, in the sections this frame reads back line
 *  for line. */
export function planRequest(purpose: string): string {
    const why = purpose.trim();
    return [
        `${why ? `You are here for: "${why}". ` : ""}Work out with me one result you should own, a bar held every week or a finish line by a date, and keep it with set_what_you_own, with a measure once what it counts exists.`,
        `Then write the plan in ${PLAN_PATH} under ## What must be true, ## How we work, ## Who holds what and ## Never: one line per bullet, each ending with who said it and when. No front matter: what you own is kept on the Lemma.`,
        "Only write lines that name something that exists here or that you are about to set up, and say what you built.",
    ].join(" ");
}
