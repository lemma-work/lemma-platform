"use client";
import { useCallback, useEffect, useMemo, useReducer } from "react";
import { lemma } from "@/session/client";
import { buildTurns, type PlanStepState } from "@/thread/turns";
import { resourceLabel, type DisplayResource } from "@/thread/display-resource";
import { ConversationRouter } from "./conversation-router";

export interface CallResource { id: string; toolCallId?: string; resource: DisplayResource; label: string }

export function useCallSession({ podId, conversationId: selectedId }: { podId: string; conversationId: string | null }) {
    const [, render] = useReducer((n: number) => n + 1, 0);
    const router = useMemo(() => new ConversationRouter(lemma(podId), podId, render), [podId]);
    useEffect(() => () => router.close(), [router]);
    const open = useCallback(() => router.open(selectedId), [router, selectedId]);
    const items = buildTurns(router.messages.get(router.focusedId ?? "") ?? []).flatMap(t => t.items);
    const resources = items.flatMap<CallResource>(item => item.kind === "resource"
        ? [{ id: item.id, toolCallId: item.toolCallId, resource: item.resource, label: resourceLabel(item.resource) }] : []);
    const plan = items.reduce<PlanStepState[]>((found, item) => item.kind === "plan" ? item.steps : found, []);
    return { historyMessages: () => router.messages.get(router.focusedId ?? "") ?? [], conversationId: router.focusedId, open, close: router.close, route: router.route, subscribe: router.subscribe,
        setContext: router.setContext, observeTranscript: router.observeTranscript, resources, plan, isStreaming: router.isRunning };
}
