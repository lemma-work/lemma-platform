import type { LemmaClient } from "lemma-sdk";
import { routeWithDecisions, validRouterState } from "./jev-router";

/** Shared wire types. No provider credentials or execution live in the voice model. */
export type RouteAction = "voice" | "snapshot" | "existing" | "new" | "clarify";
export interface VoiceEvent {
    id: string;
    conversationId: string | null;
    kind: "accepted" | "snapshot" | "progress" | "completed" | "failed" | "needs_input" | "clarify";
    text: string;
    speak: boolean;
    responseTo?: string;
}
export interface ConversationSnapshot {
    id: string;
    title: string;
    status: string;
    updatedAt: string;
    fetchedAt: string;
    lastRunStatus: string | null;
    error: string | null;
    messages: Array<{ role: string; text: string; at: string }>;
    plan: unknown[];
    resources: Array<{ type: string; label: string }>;
    needsInput: boolean;
    openQuestions: unknown[];
    partialText?: string;
}
export interface RouterState {
    mode: "utterance" | "event";
    utterance: string;
    transcript: string;
    podContext: string;
    focusedConversationId: string | null;
    conversations: ConversationSnapshot[];
    event?: VoiceEvent;
    recentEvents?: VoiceEvent[];
}
export interface RouteDecision {
    action: RouteAction;
    conversationId: string | null;
    delivery: "speak" | "context" | "ignore";
    /** System One's confidence. Null when the model answered: it reports none. */
    confidence: number | null;
}
/** Where one routing question is asked, and as whom: the call's pod, through
 *  the client -- and so the session -- that reads and writes its conversations. */
export interface RouteAsk {
    client: Pick<LemmaClient, "request">;
    podId: string;
}

/** Routes are decided one at a time, so one that hangs holds up every
 *  utterance behind it. */
const ROUTE_TIMEOUT_MS = 15_000;
const UNAVAILABLE = "Call routing is unavailable. No request was dispatched.";

/** Ask the backend's decisions API how to route, as the person on the call.
 *
 *  Straight from the browser, with the session every other request of the
 *  call already uses. The router holds no key any more, so a hop through this
 *  app's server would guard nothing, and it would be the one place the app
 *  forwarded a session. The size limits that hop enforced are checked here,
 *  before anything is sent. */
export async function classifyCall(state: RouterState, ask: RouteAsk, signal?: AbortSignal): Promise<RouteDecision> {
    if (!validRouterState(state)) throw new Error(UNAVAILABLE);
    const deadline = new AbortController();
    const stop = () => deadline.abort();
    const timer = setTimeout(stop, ROUTE_TIMEOUT_MS);
    signal?.addEventListener("abort", stop, { once: true });
    if (signal?.aborted) stop();
    try {
        return await routeWithDecisions(state, ask, deadline.signal);
    } catch {
        throw new Error(UNAVAILABLE);
    } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", stop);
    }
}
