"use client";

import { useCallback, useEffect, useRef } from "react";
import { MicIcon, MicOffIcon, EndCallIcon, MinimizeIcon, ChevronLeftIcon, ChevronRightIcon } from "@/ui/icons";
import { ResourceCard } from "@/thread/resource-card";
import { CallMark } from "./call-mark";
import type { CallStatus } from "./use-call";
import type { CanvasItem } from "./canvas";
import type { CallLine } from "./call-transcript";
import { statusLine } from "./call-status";

/** The call, given the whole pane.
 *
 *  A huddle, not a phone call: the point of the screen is the thing being
 *  looked at together. So the mark is the whole stage until there is
 *  something better to put there, and then it steps aside and becomes a
 *  small presence at the top while the teammate's widgets take the space.
 *
 *  Nothing here authors a picture. The screen is the call's canvas: whatever
 *  the teammate or any of its sub-agents put there, rendered by the same
 *  `ResourceCard` the transcript uses — so a diagram, a chart or a file looks
 *  the same here as it does in the conversation afterwards. */
export function CallScreen({
    teammate,
    podId,
    status,
    error,
    notice,
    muted,
    thinking,
    level,
    canvas,
    showing,
    transcript,
    onMute,
    onEnd,
    onCollapse,
    onShow,
    onRenderFailed,
    onOpenFile,
    onOpenApp,
    onOpenTable,
}: {
    teammate: string;
    podId: string;
    status: CallStatus;
    error: string | null;
    /** Something to know, said once (a request the team cannot take). */
    notice?: string | null;
    muted: boolean;
    thinking?: boolean;
    level: number;
    /** The call's canvas, in the order it was drawn. */
    canvas: CanvasItem[];
    /** The item on screen, by id; null for the newest. Held by the call, not
     *  here: this screen unmounts when the call is collapsed. */
    showing: string | null;
    /** What has been said so far, both ways. */
    transcript: CallLine[];
    onMute: () => void;
    onEnd: () => void;
    onCollapse: () => void;
    /** The person paged to this item: show it, and tell the voice. */
    onShow: (item: CanvasItem) => void;
    /** An item could not draw here; whoever drew it is told. */
    onRenderFailed?: (item: CanvasItem, problem: string) => void;
    /** Put the thing on screen onto the stage as a tab. The call does not end
     *  for it — it steps back to the bar, which is the whole point of having
     *  a bar. */
    onOpenFile?: (path: string) => void;
    onOpenApp?: (name: string) => void;
    onOpenTable?: (name: string) => void;
}) {
    /* One screen, one thing on it. Widgets were a scrolling column, which is
       not what sharing a screen looks like — a screen shows the current thing
       and you page back to the last one.

       The newest always takes the screen the moment it arrives, the way a
       slide changes when the presenter changes it. Paging back is for looking
       something up, not for holding the screen. */
    const resources = canvas;
    const newest = Math.max(0, resources.length - 1);
    const picked = showing === null ? -1 : resources.findIndex((one) => one.id === showing);
    const active = picked >= 0 ? picked : newest;
    const onScreen = resources[active];

    /* Every page the person turns is them pointing at something, back to the
       newest included: the voice is told which slide is up, or it talks about
       one while another is on screen. The newest item arriving is not a turn
       of theirs -- the voice was told about that when it was drawn. */
    const page = useCallback((to: number) => {
        const item = resources[to];
        if (item) onShow(item);
    }, [resources, onShow]);

    /* The arrow keys page, as in any slide viewer -- unless the person is
       typing, or a widget inside the slide has the focus and the keys. */
    useEffect(() => {
        if (resources.length < 2) return;
        const onKey = (event: KeyboardEvent) => {
            const target = event.target as HTMLElement | null;
            if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
            if (event.key === "ArrowLeft") page(active - 1);
            else if (event.key === "ArrowRight") page(active + 1);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [resources.length, active, page]);

    /* A widget that cannot draw says so to its parent frame (the widget kit's
       `lemma.fail`, and any error it did not catch). Which item
       it was is read off the frame that sent it, not off the message, so a
       widget cannot report a failure on another's behalf. */
    const deckRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        if (!onRenderFailed) return;
        const heard = (event: MessageEvent) => {
            const data = event.data as { type?: unknown; message?: unknown } | null;
            if (!data || data.type !== "lemma-widget-error") return;
            for (const slide of deckRef.current?.querySelectorAll<HTMLElement>("[data-canvas-item]") ?? []) {
                const frames = Array.from(slide.querySelectorAll("iframe"));
                if (!frames.some((frame) => frame.contentWindow === event.source)) continue;
                const item = resources.find((one) => one.id === slide.dataset.canvasItem);
                if (item) onRenderFailed(item, String(data.message ?? "").slice(0, 500));
            }
        };
        window.addEventListener("message", heard);
        return () => window.removeEventListener("message", heard);
    }, [resources, onRenderFailed]);

    /* One line, the one being said. A call is spoken — a scrolling record of
       it would be a second thing to read while trying to listen, and the
       conversation itself is already keeping the record. The finished line
       stays up until the next one starts, so a pause is not a blank screen. */
    const caption = transcript[transcript.length - 1] ?? null;

    /* The mark is the whole stage until there is something better to put
       there — which is now either a widget or the first thing anybody said. */
    const bare = resources.length === 0;

    const hint = statusLine({ error: error ?? notice, muted, thinking, teammate });

    return (
        <section className="call-view" aria-label={"Call with " + teammate}>
            <header className="call-view__head">
                <CallMark teammate={teammate} level={level} size={bare ? "stage" : "bar"} />
                <div className="call-view__what">
                    <h1 className="call-view__who">
                        {status === "connecting" ? "Connecting to " + teammate + "…" : "On a call with " + teammate}
                    </h1>
                    {/* Announced, because this line is how a person finds out
                        the call is muted or the run is still going. */}
                    <p className="call-view__hint" role="status">{hint}</p>
                </div>
                <button className="call-view__collapse" onClick={onCollapse} title="Minimize call and stay connected">
                    <MinimizeIcon size={18} />Minimize
                </button>
            </header>

            {/* No longer a scroller. The screen is a fixed frame and the
                deck pages inside it, so there is nothing here to follow. */}
            <div className="call-view__stage">
                {bare ? (
                    /* Not an error and not a loading state — a call that has
                       just opened has nothing on screen yet, and saying so
                       plainly beats a spinner that implies something is late. */
                    <p className="call-view__empty">
                        Anything {teammate} shows you will appear here.
                    </p>
                ) : (
                    <div className="call-deck" ref={deckRef}>
                        {/* Every widget stays mounted and all but one is
                            hidden — the same rule the pod's app tabs follow.
                            Rendering only the visible slide would tear down
                            its iframe, and paging back would reload a widget
                            that had already been looked at. */}
                        <div className="call-deck__screen">
                            {resources.map((shown, index) => (
                                <div className="call-deck__slide" key={shown.id} hidden={index !== active} data-canvas-item={shown.id}>
                                    {/* The same card the transcript uses, so a
                                        file, a table or an app the teammate
                                        shows lands on the call's screen
                                        looking like itself rather than being
                                        dropped for not being a widget. */}
                                    <ResourceCard
                                        resource={shown.resource}
                                        podId={podId}
                                        conversationId={shown.conversationId}
                                        toolCallId={shown.id}
                                        onOpenFile={onOpenFile}
                                        onOpenApp={onOpenApp}
                                        onOpenTable={onOpenTable}
                                    />
                                </div>
                            ))}
                        </div>

                        {/* Only worth showing once there is somewhere to go. */}
                        {resources.length > 1 && (
                            <div className="call-deck__nav">
                                <button
                                    className="call-deck__step"
                                    onClick={() => page(active - 1)}
                                    disabled={active === 0}
                                    aria-label="Previous"
                                ><ChevronLeftIcon size={18} /></button>
                                <span className="call-deck__where">
                                    <span className="call-deck__label">{onScreen?.label}</span>
                                    <span className="call-deck__count">{active + 1} of {resources.length}</span>
                                </span>
                                <button
                                    className="call-deck__step"
                                    onClick={() => page(active + 1)}
                                    disabled={active === newest}
                                    aria-label="Next"
                                ><ChevronRightIcon size={18} /></button>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Live, and polite rather than assertive: a caption that
                interrupted a screen reader on every syllable would be worse
                than none. Keyed by line so each new one animates in rather
                than the text swapping under a static element. */}
            <div className="call-captions" aria-live="polite" aria-label="Captions">
                {caption && (
                    <p className={"call-caption call-caption--" + caption.speaker} key={caption.id}>
                        <span className="call-caption__who">{caption.speaker === "them" ? "You" : teammate}</span>
                        <span className="call-caption__text">{caption.text}</span>
                    </p>
                )}
            </div>

            <footer className="call-view__controls">
                <button className="call-view__mute" onClick={onMute} aria-pressed={muted}>
                    {muted ? <MicOffIcon size={18} /> : <MicIcon size={18} />}{muted ? "Unmute" : "Mute"}
                </button>
                <button className="call-view__end" onClick={onEnd}>
                    <EndCallIcon size={18} />End call
                </button>
            </footer>
        </section>
    );
}
