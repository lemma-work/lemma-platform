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

/** Who a call is with, fixed when it is placed. */
export interface CallTarget {
    pod: { id: string; name: string };
    teammate: string;
}

/** Where a call is placed: the chat it continues, or a new conversation. */
export interface CallPlace extends CallTarget {
    /** The chat the call is held in; null (or a new pane) starts a new one. */
    conversationId: string | null;
}

/** A call with a pod's teammate.
 *
 *  A call is held in a conversation on the server: the chat it was started
 *  from, or a new one when it was started from nowhere -- so the call, the
 *  teammate's work and the transcript are one chat, the one the person was
 *  in. Its teammate coordinates the call there, hands work to sub-agents, and
 *  puts what is worth seeing on the call's canvas.
 *
 *  Where it is placed is passed to `start`, not read from the hook's
 *  arguments: the call must not follow the person to another pod, and a call
 *  ended by the server must not leave the next one aimed at the last pod. */
export function useHuddle() {
    const call = useCall();
    /** Expanded is the screen; collapsed is the bar. A call is never gone
     *  just because you are looking at something else. */
    const [expanded, setExpanded] = useState(false);
    const [callId, setCallId] = useState<string | null>(null);
    const [target, setTarget] = useState<CallTarget | null>(null);
    /** A call is being set up or is on: a second press opens it rather than
     *  placing another, which would have no connection of its own. */
    const busy = useRef(false);
    const [starting, setStarting] = useState(false);
    const [setupError, setSetupError] = useState<string | null>(null);
    const setupAttempt = useRef(0);
    const queryClient = useQueryClient();

    /* The pod and the chat the call continues, for the voice -- the teammate
       has the chat itself. A pod or chat that cannot be read is a call without
       the background, worse but not broken, so neither failure stops it. */
    const context = useCallback(async (place: CallPlace) => {
        const parts: string[] = [];
        const profile = await queryClient.ensureQueryData({
            queryKey: ["profile", place.pod.id],
            queryFn: () => source.getProfile(place.pod.id),
            staleTime: 5 * 60_000,
        }).catch(() => null);
        if (profile) parts.push(podBrief(profile, place.teammate));
        if (place.conversationId && place.conversationId !== NEW_CONVERSATION) {
            const page = await lemma(place.pod.id).conversations.messages
                .list(place.conversationId, { pod_id: place.pod.id, limit: 80 })
                .catch(() => null);
            const history = page ? historyBrief(buildTurns(page.items), place.teammate) : "";
            if (history) parts.push(history);
        }
        return parts.join("\n\n");
    }, [queryClient]);

    const { start: startCall, end: endCall } = call;

    /** Place a call, or open the one already on. Resolves to the call's
     *  conversation once it is placed, for the shell to show; null when a
     *  call was already on or placing it failed. */
    const start = useCallback(async (place: CallPlace): Promise<string | null> => {
        if (busy.current || call.active) { setExpanded(true); return null; }
        busy.current = true;
        setStarting(true);
        const attempt = ++setupAttempt.current;
        setSetupError(null);
        setExpanded(true);
        setTarget({ pod: place.pod, teammate: place.teammate });
        const existing = place.conversationId && place.conversationId !== NEW_CONVERSATION
            ? place.conversationId : null;
        try {
            const opened = await lemma(place.pod.id).calls.open({
                ...(existing ? { conversation_id: existing } : {}),
                display_name: place.teammate || "your teammate",
                title: `Call with ${place.teammate || "your teammate"}`,
            });
            if (attempt !== setupAttempt.current) return null;
            setCallId(opened.call_id);
            const brief = await context(place);
            if (attempt !== setupAttempt.current) return null;
            void startCall(place.pod.id, opened.call_id, { teammate: place.teammate, pod: place.pod.name, context: brief });
            return opened.call_id;
        } catch (problem) {
            if (attempt !== setupAttempt.current) return null;
            setCallId(null);
            setTarget(null);
            setSetupError(problem instanceof Error ? problem.message : "Could not start the call.");
            setExpanded(false);
            return null;
        } finally {
            if (attempt === setupAttempt.current) { busy.current = false; setStarting(false); }
        }
    }, [context, startCall, call.active]);

    const end = useCallback(() => {
        setupAttempt.current++;
        busy.current = false;
        setStarting(false);
        setSetupError(null);
        setCallId(null);
        setTarget(null);
        endCall();
        setExpanded(false);
    }, [endCall]);

    // Ended from the other side -- the server, the network -- as well as here:
    // the next call is placed from wherever the person is then.
    useEffect(() => {
        if (call.status === "ended" || call.status === "error") {
            setCallId(null);
            setTarget(null);
        }
    }, [call.status]);

    return {
        status: call.status,
        error: setupError ?? call.error,
        notice: call.notice,
        muted: call.muted,
        transcript: call.transcript,
        /** Someone in the call is working: "still on it" means work is running. */
        thinking: call.working,
        liveEpoch: call.liveEpoch,
        level: call.level,
        active: call.active,
        canvas: call.canvas,
        showing: call.showing,
        target,
        callConversationId: callId,
        settled: call.settled,
        /** A call is on or being set up: pressing call again should open it. */
        inCall: starting || call.active,
        expanded: expanded && call.active,
        expand: useCallback(() => setExpanded(true), []),
        collapse: useCallback(() => setExpanded(false), []),
        start,
        end,
        toggleMute: call.toggleMute,
        show: call.show,
        renderFailed: call.renderFailed,
    };
}

export type Huddle = ReturnType<typeof useHuddle>;
