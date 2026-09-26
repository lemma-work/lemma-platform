"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { buildTurns } from "@/thread/turns";
import { historyBrief } from "./history-brief";
import { podBrief } from "./pod-brief";
import { useCall } from "./use-call";
import { useCallSession } from "./use-call-session";

/** How much of the written conversation to read before the call opens. */
/** Voice and conversation routing persist at pod level during a call. */
export function useHuddle({ pod, teammate, conversationId }: {
    pod: { id: string; name: string };
    teammate: string;
    /** The conversation open when the call starts. */
    conversationId: string | null;
}) {
    const backend = useCallSession({ podId: pod.id, conversationId });
    /** Expanded is the screen; collapsed is the bar. A call is never gone
     *  just because you are looking at something else. */
    const [expanded, setExpanded] = useState(false);

    /* Read when a call starts, not on every pod you look at. The profile is
       seven round trips — as a standing `useQuery` here it would have fired
       them for every pod in the app on the chance somebody might call, and
       with an empty id before a pod had even resolved.

       `ensureQueryData` shares the profile tab's cache both ways: a pod whose
       profile has been opened starts its call with this already in hand, and a
       call warms the tab. */
    const queryClient = useQueryClient();
    const context = useCallback(async () => {
        if (!pod.id) return "";
        const profile = await queryClient.ensureQueryData({
            queryKey: ["profile", pod.id],
            queryFn: () => source.getProfile(pod.id),
            staleTime: 5 * 60_000,
        });
        const brief = podBrief(profile, teammate);
        backend.setContext(brief);
        return brief;
    }, [queryClient, pod.id, teammate, backend.setContext]);

    const history = () => historyBrief(buildTurns(backend.historyMessages()), teammate);

    const call = useCall({
        teammate,
        pod: pod.name,
        handlers: { observeTranscript: backend.observeTranscript, route: backend.route, subscribe: backend.subscribe, history, context },
    });

    const { start: startCall, end: endCall } = call;
    const { open: openConversation, close: closeConversation } = backend;

    /* Kept, not swallowed. Failing to open the call's conversation breaks
       every other part of a call, so it is the last failure that should be
       discarded into a `.catch(() => undefined)`: the voice still connects,
       the person still talks, and nothing ever comes back with no symptom
       anywhere to explain it. */
    const [setupError, setSetupError] = useState<string | null>(null);
    const setupAttempt = useRef(0);
    const setupPending = useRef(false);
    useEffect(() => () => { setupAttempt.current++; }, []);

    const start = useCallback(async () => {
        if (setupPending.current) return;
        setupPending.current = true;
        const attempt = ++setupAttempt.current;
        setSetupError(null);
        setExpanded(true);
        // Load routing candidates before opening audio.
        try {
            const config = await fetch("/api/call/config").then(response => response.json());
            if (attempt !== setupAttempt.current) return;
            /* The button is only offered once this is known to be true (see
               `useVoiceConfigured`); reaching here means the setting changed
               since. Said to the person, not the operator. */
            if (!config.configured) throw new Error("Voice calls aren’t set up on this install.");
            await openConversation();
            if (attempt !== setupAttempt.current) return;
            await startCall();
        } catch (problem) {
            if (attempt !== setupAttempt.current) return;
            setSetupError(problem instanceof Error ? problem.message : "Could not open the call's conversation.");
            setExpanded(false);
            return;
        } finally { if (attempt === setupAttempt.current) setupPending.current = false; }
    }, [openConversation, startCall]);

    const end = useCallback(() => {
        setupAttempt.current++; setupPending.current = false;
        setSetupError(null);
        endCall();
        closeConversation();
        setExpanded(false);
    }, [endCall, closeConversation]);

    /* Announced once, by name and kind, the moment it exists. Tracking what
       has been said keeps a re-render from telling the voice the same chart
       appeared four times. The kind matters out loud: "the vendor table is on
       your screen" and "the file is on your screen" are different sentences,
       and the voice cannot tell them apart from a label alone. */
    const announcedRef = useRef(new Set<string>());
    const { announceWidget } = call;
    useEffect(() => {
        if (!call.active) return;
        for (const shown of backend.resources) {
            if (announcedRef.current.has(shown.id)) continue;
            announcedRef.current.add(shown.id);
            announceWidget(`${shown.resource.type.toLowerCase()} — ${shown.label}`);
        }
    }, [call.active, backend.resources, announceWidget]);

    useEffect(() => {
        if (!call.active) announcedRef.current = new Set();
    }, [call.active]);

    useEffect(() => {
        if (call.status === "ended" || call.status === "error") closeConversation();
    }, [call.status, closeConversation]);

    const expand = useCallback(() => setExpanded(true), []);
    const collapse = useCallback(() => setExpanded(false), []);

    return {
        status: call.status,
        error: setupError ?? call.error,
        muted: call.muted,
        transcript: call.transcript,
        plan: backend.plan,
        /* Two sources, because the lighter model is silent about itself.
           `gemini-3.8-live` never sends `interactionStatus` — only the
           extended-thinking variant does — so on the default model
           `call.thinking` is always false.

           The teammate's own run is the better signal anyway: "still working
           on it" should mean the work is still running, not that a voice model
           is still reasoning about what to say. */
        thinking: call.thinking || backend.isStreaming,
        level: call.level,
        active: call.active,
        resources: backend.resources,
        callConversationId: backend.conversationId,
        expanded: expanded && call.active,
        expand,
        collapse,
        start,
        end,
        toggleMute: call.toggleMute,
    };
}

export type Huddle = ReturnType<typeof useHuddle>;
