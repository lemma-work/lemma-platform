import { ArrowDownIcon } from "@/ui/icons";
import { useCallback, useEffect, useRef } from "react";
import { useTranscriptScroll } from "./use-transcript-scroll";
import { runFailure, transcriptState } from "./transcript-state";
import { openSettings } from "@/desktop/open-settings";
import { thisMacReachable } from "@/desktop/this-mac";
import { ConversationLoading } from "./conversation-loading";
import { StreamingProse } from "./markdown";
import type { Resolve } from "./interaction-card";
import { liveNote, type Streaming, type Turn } from "./turns";
import type { Persona } from "@/data";
import { AddModelAction, OpenModelsAction } from "./add-model-action";
import { noModelSentence } from "./model-setup";
import { Notes, Reply, TurnRow } from "./turn-row";
import { Mark } from "@/shell/mark";


/** A run that has started and said nothing yet. One object, so the turn
 *  holding it is not redrawn for a new empty one every render. */
const NOTHING_YET: Streaming = { text: "", thinking: "", tool: null };

export function Transcript({
    turns,
    teammate,
    speakerSeed,
    streaming,
    state,
    error,
    emptyTitle,
    emptyBody,
    hasMore,
    loadingEarlier,
    loading = false,
    onReload,
    reloadLabel = "Retry",
    podId,
    conversationId,
    onOpenApp,
    onOpenFile,
    onOpenTable,
    onEarlier,
    onResolve,
    onRetry,
    noModel = false,
    modelsAction = false,
    dockedId,
    outsiders,
    onRetrySend,
    onEditSend,
}: {
    turns: Turn[];
    teammate: Persona;
    /** The seed the speaker's face is drawn from; the pod's own by default. */
    speakerSeed?: string;
    streaming: Streaming | null;
    state: "idle" | "running" | "waiting" | "failed";
    error: string | null;
    emptyTitle: string;
    emptyBody: string;
    hasMore?: boolean;
    /** A page of older messages is on its way. The control has to say so: it
     *  sits at the very top, where a click that changes nothing for a second
     *  reads as a dead button. */
    loadingEarlier?: boolean;
    loading?: boolean;
    onReload?: () => void;
    /** What the reload control says: "Retry" for a load that failed, or what
     *  the caller is actually offering. */
    reloadLabel?: string;
    podId: string;
    conversationId?: string | null;
    onOpenApp?: (name: string) => void;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
    /** Resolves false when there was nothing older to get, so the viewport is
     *  left alone rather than corrected against a load that never happened. */
    onEarlier?: () => void | boolean | Promise<void | boolean>;
    onResolve?: Resolve;
    onRetry?: () => void;
    /** The failure is "this teammate has no model". Said in the teammate's
     *  name with the one action that fixes it, and without "Try again",
     *  which would fail the same way. */
    noModel?: boolean;
    /** The error's fix is in Settings → Models; draws the way there. */
    modelsAction?: boolean;
    /** The pause `InteractionDock` is holding above the composer. It is drawn
     *  there instead of here, so here it is skipped — the alternative is the
     *  same live card twice, with two sets of buttons and one of them scrolled
     *  out of sight. Nothing is lost by the gap: an open pause is always the
     *  last thing in the transcript, because the run is stopped behind it. */
    dockedId?: string | null;
    /** The space's name, in a conversation where people outside it ask. Set,
     *  each of their messages carries who wrote it and that they are not in it. */
    outsiders?: string;
    /** A message the server refused, sent again or taken back into the
     *  composer. Stable across renders, or every turn redraws. */
    onRetrySend?: (id: string) => void;
    onEditSend?: (id: string) => void;
}) {
    /* Following the bottom is its own problem, and a harder one than it looks:
       a threshold on distance cannot tell "the reader scrolled up" from "a tool
       card just added 284px", and both happen constantly. The hook is the
       platform's, ported whole — it watches for a *gesture* instead, and keeps
       the reader's place across reflows. */
    /* Reaching the top is a request for older messages, and it has to go
       through `preserveAcross`: prepending a page moves every row down by its
       height, and a reader who was mid-sentence would be carried with it. The
       hook keeps the row they were on exactly where it was. Held in a ref
       because the hook needs a handler before the handler can reference the
       hook. */
    const earlierRef = useRef<() => void>(() => {});
    const scroll = useTranscriptScroll({
        activeConversationId: conversationId ?? null,
        onReachTop: () => earlierRef.current(),
    });
    const goEarlier = useCallback(() => {
        if (!onEarlier) return;
        void scroll.preserveAcross(async () => (await onEarlier()) !== false);
    }, [onEarlier, scroll]);
    useEffect(() => {
        earlierRef.current = goEarlier;
    }, [goEarlier]);

    const display = transcriptState({ loading, hasTurns: turns.length > 0, hasStreamingText: Boolean(streaming?.text), error });
    const live = Boolean(streaming && (streaming.text || streaming.thinking || streaming.tool));
    /* A run is drawn as the reply it will become from the moment it starts —
       from the moment the message is sent, in fact — not only once its first
       token arrives. Waiting for the token put three different things on
       screen in a row: a separate "is working…" line, then nothing at all
       while the stream opened with nothing in it yet, then the reply. Now the
       last turn's reply is the one live element throughout, empty and
       "Working" until there is something to show. */
    const working = state === "running" && !loading;
    const lastIndex = turns.length - 1;
    const mergeInto = (live || working) && lastIndex >= 0 ? lastIndex : -1;
    const inFlight = streaming ?? (working ? NOTHING_YET : null);
    const runEnded = !loading && state !== "running";

    return (
        <div aria-busy={loading} className="pane convo-scroll" ref={scroll.containerRef} onScroll={scroll.onScroll}>
            <div className="pane__inner">
                {hasMore && (
                    <button className="earlier" onClick={goEarlier} disabled={loadingEarlier}>
                        {loadingEarlier ? "Loading earlier…" : "↑ Earlier"}
                    </button>
                )}

                {display === "loading" && <ConversationLoading />}

                {display === "empty" && (
                    <div className="quiet">
                        <Mark seed={speakerSeed ?? podId} name={teammate.name} icon={teammate.iconUrl} size={44} greeting={1} />
                        <h2>{emptyTitle}</h2>
                        <p>{emptyBody}</p>
                    </div>
                )}

                <div className="convo">
                    {turns.map((turn, index) => (
                        <TurnRow
                            key={turn.id}
                            turn={turn}
                            streaming={index === mergeInto ? inFlight : null}
                            teammate={teammate}
                            seed={speakerSeed ?? podId}
                            podId={podId}
                            conversationId={conversationId}
                            outsiders={outsiders}
                            dockedId={dockedId}
                            runEnded={runEnded}
                            onOpenApp={onOpenApp}
                            onOpenFile={onOpenFile}
                            onOpenTable={onOpenTable}
                            onResolve={onResolve}
                            onRetrySend={onRetrySend}
                            onEditSend={onEditSend}
                        />
                    ))}

                    {(live || working) && mergeInto < 0 && inFlight && (
                        <Reply seed={speakerSeed ?? podId} teammate={teammate}>
                            <Notes
                                notes={liveNote(inFlight)}
                                live={!inFlight.text}
                                podId={podId}
                                conversationId={conversationId}
                            />
                            {inFlight.text && (
                                <div className="said">
                                    <StreamingProse text={inFlight.text} />
                                </div>
                            )}
                        </Reply>
                    )}
                </div>

                {state === "failed" && !onReload && (() => {
                    const failure = runFailure(error);
                    return (
                        <div className="failed">
                            <span>{noModel ? noModelSentence(teammate.name) : failure.text}</span>
                            {noModel ? <AddModelAction /> : modelsAction && <OpenModelsAction />}
                            {onRetry && !noModel && (
                                <button className="btn" onClick={onRetry}>
                                    Try again
                                </button>
                            )}
                            {/* Where the computer and its agents are managed:
                                This Mac on a local install's own window, the
                                Models page anywhere else. */}
                            {!noModel && failure.codingAgents && (
                                <button
                                    className="linkish"
                                    onClick={() => openSettings(thisMacReachable() ? "this-mac-agents" : "models")}
                                >
                                    Coding agents settings
                                </button>
                            )}
                        </div>
                    );
                })()}

                {error && (state !== "failed" || onReload) && (
                    <div className="conversation-error" role="alert">
                        <p>{noModel ? noModelSentence(teammate.name) : error}</p>
                        {noModel ? <AddModelAction /> : modelsAction && <OpenModelsAction />}
                        {onReload && <button className="earlier" onClick={onReload} disabled={loading}>{reloadLabel}</button>}
                    </div>
                )}
            </div>

            {/* Only while the reader has actually left the bottom. A jump
                control that is always there is a control that is usually
                wrong, and it covers the newest message to say so. */}
            {!scroll.isFollowing && (
                <button
                    className="convo-jump"
                    onClick={() => scroll.scrollToBottom("smooth")}
                    aria-label="Jump to the newest message"
                    title="Jump to the newest message"
                >
                    <ArrowDownIcon size={16} />
                </button>
            )}
        </div>
    );
}
