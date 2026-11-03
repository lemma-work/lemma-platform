"use client";

import { useCallback, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { useHuddle, type Huddle } from "@/call/use-huddle";
import { CallScreen } from "@/call/call-screen";
import { CallBar } from "@/call/call-bar";
import { refreshConversationLists } from "@/thread/conversation-list";
import { chatView, type Go } from "./model";

/** What a chat needs of a voice call: placing one, and what to show of it. */
export interface Calls {
    /** Call a Lemma, continuing this chat, or a new one when it has none
     *  yet. The chat the call is held in opens. */
    start: (pod: Pod, conversationId: string | null) => void;
    error: string | null;
    /** Changes whenever the chat a call is held in should read its run again
     *  -- each time the call connects or work starts, and once it settles --
     *  so the Lemma and its agents are seen working, live. Empty for any
     *  other chat. */
    refreshFor: (conversationId: string) => string;
}

/** The workspace's huddle, held by the frame: one call at a time, with the
 *  Lemma it was placed with, for as long as it lasts -- walking into another
 *  Lemma mid-call neither re-points it nor relabels it. */
export function useCalls(go: Go): { huddle: Huddle; calls: Calls } {
    const huddle = useHuddle();
    const queryClient = useQueryClient();
    const { start: place } = huddle;
    const start = useCallback((pod: Pod, conversationId: string | null) => {
        void place({ pod: { id: pod.id, name: pod.name }, teammate: pod.teammate?.name || pod.name, conversationId }).then((placed) => {
            if (!placed) return;
            go(chatView({ kind: "chat", podId: pod.id, id: placed }));
            void refreshConversationLists(queryClient, pod.id);
        });
    }, [place, go, queryClient]);
    const { callConversationId: held, liveEpoch, active, settled, error } = huddle;
    const calls = useMemo<Calls>(() => ({
        start,
        error,
        refreshFor: (conversationId) => (held === conversationId
            ? `live:${liveEpoch}:${active}`
            : settled?.callId === conversationId ? `settled:${settled.count}` : ""),
    }), [start, error, held, liveEpoch, active, settled]);
    return { huddle, calls };
}

/** The call given the left of the main side, its chat on the right: what is
 *  said, what it shows, and the controls. Opening something it shows steps
 *  back to the bar first, so the page is not opened behind the call. */
export function CallStage({ huddle, go }: { huddle: Huddle; go: Go }) {
    const target = huddle.target;
    if (!target) return null;
    const podId = target.pod.id;
    return (
        <div className="ox-callstage">
            <CallScreen
                teammate={target.teammate}
                podId={podId}
                status={huddle.status}
                error={huddle.error}
                notice={huddle.notice}
                muted={huddle.muted}
                thinking={huddle.thinking}
                level={huddle.level}
                canvas={huddle.canvas}
                showing={huddle.showing}
                transcript={huddle.transcript}
                onMute={huddle.toggleMute}
                onEnd={huddle.end}
                onCollapse={huddle.collapse}
                onShow={huddle.show}
                onRenderFailed={huddle.renderFailed}
                onOpenFile={(path) => { huddle.collapse(); go({ name: "file", podId, path }); }}
                onOpenApp={(app) => { huddle.collapse(); go({ name: "app", podId, app }); }}
                onOpenTable={(table) => { huddle.collapse(); go({ name: "table", podId, table }); }}
            />
        </div>
    );
}

/** A call that is on while something else is in front: who it is with,
 *  mute, end, and the way back to it. */
export function CallDock({ huddle }: { huddle: Huddle }) {
    if (!huddle.active || huddle.expanded) return null;
    return (
        <CallBar
            teammate={huddle.target?.teammate ?? ""}
            status={huddle.status}
            error={huddle.error}
            notice={huddle.notice}
            muted={huddle.muted}
            thinking={huddle.thinking}
            level={huddle.level}
            onMute={huddle.toggleMute}
            onEnd={huddle.end}
            onExpand={huddle.expand}
        />
    );
}
