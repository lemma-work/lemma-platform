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
    confidence: number;
}

export async function classifyCall(state: RouterState, signal?: AbortSignal): Promise<RouteDecision> {
    const response = await fetch("/api/call/route", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(state), signal,
    });
    if (!response.ok) throw new Error("Call routing is unavailable. No request was dispatched.");
    return response.json();
}
