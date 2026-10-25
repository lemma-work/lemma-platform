"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { NEW_CONVERSATION } from "@/data/types";
import { lemma } from "@/session/client";
import { buildTurns } from "@/thread/turns";
import { historyBrief } from "./history-brief";
import { podBrief } from "./pod-brief";
import { useCall } from "./use-call";

/** A call with the pod's teammate.
 *
 *  Opening one makes a conversation on the server that *is* the call: the
 *  teammate coordinates it there, hands work to its sub-agents, and puts what
 *  is worth seeing on the call's canvas. What was on screen when the call
 *  started goes with it as context, so the call continues that conversation
 *  rather than starting cold. */
export function useHuddle({ pod, teammate, conversationId }: {
    pod: { id: string; name: string };
    teammate: string;
    /** The conversation open when the call starts. */
    conversationId: string | null;
}) {
    const call = useCall({ podId: pod.id });
    /** Expanded is the screen; collapsed is the bar. A call is never gone
     *  just because you are looking at something else. */
    const [expanded, setExpanded] = useState(false);
    const [callId, setCallId] = useState<string | null>(null);
    /** The last call's conversation, kept after it ends: its transcript lands
     *  there once the server has finished with it. */
    const [lastCallId, setLastCallId] = useState<string | null>(null);
    const [setupError, setSetupError] = useState<string | null>(null);
    const setupAttempt = useRef(0);
    const queryClient = useQueryClient();

    /* The pod and the conversation the call grew out of, for the voice. A pod
       that cannot be read is a call without the background -- worse, not
       broken -- so neither failure stops the call. */
    const context = useCallback(async () => {
        const parts: string[] = [];
        const profile = await queryClient.ensureQueryData({
            queryKey: ["profile", pod.id],
            queryFn: () => source.getProfile(pod.id),
            staleTime: 5 * 60_000,
        }).catch(() => null);
        if (profile) parts.push(podBrief(profile, teammate));
        if (conversationId && conversationId !== NEW_CONVERSATION) {
            const page = await lemma(pod.id).conversations.messages.list(conversationId, { pod_id: pod.id, limit: 80 }).catch(() => null);
            const history = page ? historyBrief(buildTurns(page.items), teammate) : "";
            if (history) parts.push(history);
        }
        return parts.join("\n\n");
    }, [queryClient, pod.id, teammate, conversationId]);

    const { start: startCall, end: endCall } = call;

    const start = useCallback(async () => {
        const attempt = ++setupAttempt.current;
        setSetupError(null);
        setExpanded(true);
        try {
            const opened = await lemma(pod.id).calls.open({
                display_name: teammate || "your teammate",
                title: `Call with ${teammate || "your teammate"}`,
            });
            if (attempt !== setupAttempt.current) return;
            setCallId(opened.call_id);
            setLastCallId(opened.call_id);
            const brief = await context();
            if (attempt !== setupAttempt.current) return;
            await startCall(opened.call_id, { teammate, pod: pod.name, context: brief });
        } catch (problem) {
            if (attempt !== setupAttempt.current) return;
            setCallId(null);
            setSetupError(problem instanceof Error ? problem.message : "Could not start the call.");
            setExpanded(false);
        }
    }, [pod.id, pod.name, teammate, context, startCall]);

    /* The call's id goes with it: the shell adopts the call's conversation
       while one is set, and an ended call must not pull the person back to it
       when they open something else. */
    const end = useCallback(() => {
        setupAttempt.current++;
        setSetupError(null);
        setCallId(null);
        endCall();
        setExpanded(false);
    }, [endCall]);

    // Ended from the other side -- the server, the network -- as well as here.
    useEffect(() => {
        if (call.status === "ended" || call.status === "error") setCallId(null);
    }, [call.status]);


    return {
        status: call.status,
        error: setupError ?? call.error,
        muted: call.muted,
        transcript: call.transcript,
        /* The coordinator's own run is the signal: "still working on it"
           should mean the work is still running. */
        thinking: call.working,
        level: call.level,
        active: call.active,
        canvas: call.canvas,
        callConversationId: callId,
        lastCallId,
        settled: call.settled,
        expanded: expanded && call.active,
        expand: useCallback(() => setExpanded(true), []),
        collapse: useCallback(() => setExpanded(false), []),
        start,
        end,
        toggleMute: call.toggleMute,
        looking: call.looking,
        renderFailed: call.renderFailed,
    };
}

export type Huddle = ReturnType<typeof useHuddle>;
