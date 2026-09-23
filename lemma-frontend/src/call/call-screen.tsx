"use client";

import { useEffect, useState } from "react";
import { MicIcon, MicOffIcon, EndCallIcon, MinimizeIcon, ChevronLeftIcon, ChevronRightIcon } from "@/ui/icons";
import { ResourceCard } from "@/thread/resource-card";
import { CallMark } from "./call-mark";
import type { CallStatus } from "./use-call";
import type { CallResource } from "./use-call-session";
import type { CallLine } from "./call-transcript";
import type { PlanStepState } from "@/thread/turns";
import { statusLine } from "./call-status";

/** The call, given the whole pane.
 *
 *  A huddle, not a phone call: the point of the screen is the thing being
 *  looked at together. So the mark is the whole stage until there is
 *  something better to put there, and then it steps aside and becomes a
 *  small presence at the top while the teammate's widgets take the space.
 *
 *  Nothing here authors a picture. Every widget on this screen came from the
 *  teammate's own `display_resource`, rendered by the same `WidgetView` the
 *  transcript uses — which is what gets it the theme, the negotiated height
 *  and the compose bridge, instead of the white-backed `srcDoc` box the call
 *  drew for itself before. */
export function CallScreen({
    teammate,
    podId,
    conversationId,
    status,
    error,
    muted,
    thinking,
    level,
    resources,
    transcript,
    plan,
    onMute,
    onEnd,
    onCollapse,
    onOpenFile,
    onOpenApp,
    onOpenTable,
}: {
    teammate: string;
    podId: string;
    /** The call's own conversation. Null until it exists, and a widget cannot
     *  be minted without it. */
    conversationId: string | null;
    status: CallStatus;
    error: string | null;
    muted: boolean;
    thinking?: boolean;
    level: number;
    resources: CallResource[];
    /** What has been said so far, both ways. */
    transcript: CallLine[];
    /** The teammate's own plan, for the line under its name. */
    plan?: PlanStepState[];
    onMute: () => void;
    onEnd: () => void;
    onCollapse: () => void;
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
    const [showing, setShowing] = useState(0);
    const newest = Math.max(0, resources.length - 1);
    useEffect(() => { setShowing(newest); }, [newest]);
    const active = Math.min(showing, newest);
    const onScreen = resources[active];

    /* One line, the one being said. A call is spoken — a scrolling record of
       it would be a second thing to read while trying to listen, and the
       conversation itself is already keeping the record. The finished line
       stays up until the next one starts, so a pause is not a blank screen. */
    const caption = transcript[transcript.length - 1] ?? null;

    /* The mark is the whole stage until there is something better to put
       there — which is now either a widget or the first thing anybody said. */
    const bare = resources.length === 0;

    const hint = statusLine({ error, muted, plan, thinking, teammate });

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
                    <div className="call-deck">
                        {/* Every widget stays mounted and all but one is
                            hidden — the same rule the pod's app tabs follow.
                            Rendering only the visible slide would tear down
                            its iframe, and paging back would reload a widget
                            that had already been looked at. */}
                        <div className="call-deck__screen">
                            {resources.map((shown, index) => (
                                <div className="call-deck__slide" key={shown.id} hidden={index !== active}>
                                    {/* The same card the transcript uses, so a
                                        file, a table or an app the teammate
                                        shows lands on the call's screen
                                        looking like itself rather than being
                                        dropped for not being a widget. */}
                                    <ResourceCard
                                        resource={shown.resource}
                                        podId={podId}
                                        conversationId={conversationId}
                                        toolCallId={shown.toolCallId}
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
                                    onClick={() => setShowing(active - 1)}
                                    disabled={active === 0}
                                    aria-label="Previous"
                                ><ChevronLeftIcon size={18} /></button>
                                <span className="call-deck__where">
                                    <span className="call-deck__label">{onScreen?.label}</span>
                                    <span className="call-deck__count">{active + 1} of {resources.length}</span>
                                </span>
                                <button
                                    className="call-deck__step"
                                    onClick={() => setShowing(active + 1)}
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
