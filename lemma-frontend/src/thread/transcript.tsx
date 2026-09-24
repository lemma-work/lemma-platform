import { CheckIcon, ChevronDownIcon, ChevronRightIcon, SendIcon } from "@/ui/icons";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { TRANSCRIPT_ROW_ATTRIBUTE, useTranscriptScroll } from "./use-transcript-scroll";
import { Prose } from "./markdown";
import { Mark } from "@/shell/mark";
import { ResourceCard } from "./resource-card";
import { PlanCard } from "./plan-card";
import { ToolCardView } from "./tool-card-view";
import { InteractionCard, type Resolve } from "./interaction-card";
import { liveNote, spanOf, type Note, type Streaming, type Turn } from "./turns";
import type { Persona } from "@/data";


/** The turn's work, as one line.
 *
 *  A finished run says what it cost — "Worked for 2m 14s · 7 steps" — because
 *  that is the question a person actually has about a reply that took a while,
 *  and answering it in the closed state is what makes the closed state
 *  acceptable. A live run says what it is doing right now instead; the duration
 *  is not interesting yet, and a ticking clock over a working agent reads as a
 *  countdown. */
function Notes({
    notes,
    live,
    span,
    podId,
    conversationId,
}: {
    notes: Note[];
    live?: boolean;
    span?: string;
    /** Handed to the cards inside the fold. Only the image card uses them, and
     *  only when somebody asks it to fetch: a `view_image` names a file in one
     *  of two stores, and neither can be read without knowing the pod, or — for
     *  a relative workspace path — the conversation it resolves against. */
    podId: string;
    conversationId?: string | null;
}) {
    const [open, setOpen] = useState(false);
    if (notes.length === 0) return null;
    const last = notes[notes.length - 1];
    const count = notes.length + " step" + (notes.length === 1 ? "" : "s");
    /* What the closed row says, and it depends on whether the run is still
       going.
     *
     *  Live: the agent's own statement of intent for the step happening now.
     *  "Fix boot timing and re-render" beats "Browser screenshot", and beats
     *  the thinking beside it even more — a thought is the agent working
     *  something out, often mid-dead-end, and watching that scroll past is not
     *  the same as being told what is happening.
     *
     *  Finished: the last step, as before. Not the last comment — a run of
     *  forty-six steps whose last comment came at step twelve would present
     *  that sentence as a summary of the whole thing, which is a claim nobody
     *  made. What a finished run owes you is what it cost, and the count
     *  beside this says that. */
    const saying = live ? [...notes].reverse().find((note) => note.said && note.detail) : undefined;
    const peek = saying ? saying.detail : last.label;

    return (
        <div className="steps" data-live={live ? "" : undefined}>
            <button className="steps__toggle" onClick={() => setOpen((was) => !was)} aria-expanded={open}>
                <span className="steps__state">{live ? <span className="steps__pulse" /> : <CheckIcon size={11} />}</span>
                <span className="steps__what">{live ? "Working" : span ? "Worked for " + span + " · " + count : count}</span>
                {!open && <span className="steps__peek">{peek}</span>}
                <span className="steps__chev">{open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}</span>
            </button>
            {open && (
                <ol className="steps__list">
                    {notes.map((note, index) => (
                        <li key={index} data-kind={note.kind} data-card={note.card ? "" : undefined}>
                            {note.card ? (
                                /* The step, read rather than summarised. Same
                                   row it always was — this is what opening the
                                   fold is for, and the reason the card is in
                                   here rather than out in the transcript. */
                                <ToolCardView card={note.card} podId={podId} conversationId={conversationId} />
                            ) : (
                                <>
                                    <span className="steps__label">{note.label}</span>
                                    {note.detail && (
                                        <span className={"steps__detail" + (note.said ? " steps__detail--said" : "")}>
                                            {note.detail}
                                        </span>
                                    )}
                                </>
                            )}
                        </li>
                    ))}
                </ol>
            )}
        </div>
    );
}

function Reply({ teammate, seed, at, children }: { seed: string; teammate: Persona; at?: string; children: ReactNode }) {
    return (
        <div className="msg msg--reply">
            <Mark seed={seed} name={teammate.name} icon={teammate.iconUrl} size={30} />
            <div className="msg__col">
                <div className="msg__head">
                    <span className="msg__who msg__who--teammate">{teammate.name}</span>
                    {at && <span className="msg__at">{at}</span>}
                </div>
                <div className="msg__body">{children}</div>
            </div>
        </div>
    );
}

export function Transcript({
    turns,
    teammate,
    streaming,
    state,
    error,
    emptyTitle,
    emptyBody,
    hasMore,
    loadingEarlier,
    detail,
    podId,
    conversationId,
    onOpenApp,
    onOpenFile,
    onOpenTable,
    onEarlier,
    onResolve,
    onRetry,
    dockedId,
}: {
    turns: Turn[];
    teammate: Persona;
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
    /** Shown under the empty state — what the session actually holds, so an
     *  empty transcript is never ambiguous between "nothing was said" and
     *  "nothing loaded". */
    detail?: string;
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
    /** The pause `InteractionDock` is holding above the composer. It is drawn
     *  there instead of here, so here it is skipped — the alternative is the
     *  same live card twice, with two sets of buttons and one of them scrolled
     *  out of sight. Nothing is lost by the gap: an open pause is always the
     *  last thing in the transcript, because the run is stopped behind it. */
    dockedId?: string | null;
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

    const empty = turns.length === 0 && !streaming?.text && !error;
    const live = Boolean(streaming && (streaming.text || streaming.thinking || streaming.tool));
    /* The run in flight belongs to the last turn — unless that turn is a
       message you just sent and nothing has come back for it yet, in which
       case the reply is genuinely new. */
    const lastIndex = turns.length - 1;
    const mergeInto = live && lastIndex >= 0 ? lastIndex : -1;

    return (
        <div className="pane convo-scroll" ref={scroll.containerRef} onScroll={scroll.onScroll}>
            <div className="pane__inner">
                {hasMore && (
                    <button className="earlier" onClick={goEarlier} disabled={loadingEarlier}>
                        {loadingEarlier ? "Loading earlier…" : "↑ Earlier"}
                    </button>
                )}

                {empty && (
                    <div className="quiet">
                        <h2>{emptyTitle}</h2>
                        <p>{emptyBody}</p>
                        {detail && <span className="quiet__detail">{detail}</span>}
                    </div>
                )}

                <div className="convo">
                    {turns.map((turn, index) => {
                        const merging = index === mergeInto && streaming;
                        const notes = merging ? [...turn.notes, ...liveNote(streaming)] : turn.notes;
                        const spoke = turn.items.find((item) => item.kind === "text");

                        return (
                            <div key={turn.id} {...{ [TRANSCRIPT_ROW_ATTRIBUTE]: "" }}>
                                {turn.day && <div className="day">{turn.day}</div>}

                                {turn.notice && <div className="notice">{turn.notice}</div>}

                                {/* Attribution above the surface, the same as a
                                    reply — a name tucked *inside* the block made
                                    "cool cool" two lines tall and left the two
                                    speakers built differently for no reason. */}
                                {turn.human && (
                                    <div className="msg msg--you">
                                        <div className="msg__head">
                                            <span className="msg__who">You</span>
                                            <span className="msg__at">{turn.human.at}</span>
                                        </div>
                                        <div className="msg__body">
                                            <Prose text={turn.human.text} copyable />
                                        </div>
                                    </div>
                                )}

                                {(notes.length > 0 || turn.items.length > 0 || merging) && (
                                    <Reply
                                        seed={podId}
                                        teammate={teammate}
                                        at={spoke?.kind === "text" ? spoke.at : undefined}
                                    >
                                        <Notes
                                            notes={notes}
                                            live={Boolean(merging) && !streaming?.text}
                                            span={spanOf(turn.startedAtMs, turn.endedAtMs)}
                                            podId={podId}
                                            conversationId={conversationId}
                                        />

                                        {/* One list, in the order it happened. A
                                            question the run is blocked on must
                                            not float above the widget it was
                                            asking about. */}
                                        {turn.items.map((item) => {
                                            if (item.kind === "text") {
                                                /* Its own surface. A teammate
                                                   that says four things while it
                                                   works said four things, and
                                                   running them together into one
                                                   block is the same loss as
                                                   hiding them in the trace. */
                                                return (
                                                    <div className="said" key={item.id}>
                                                        <Prose text={item.text} copyable />
                                                    </div>
                                                );
                                            }
                                            if (item.kind === "resource") {
                                                return (
                                                    <ResourceCard
                                                        key={item.id}
                                                        resource={item.resource}
                                                        podId={podId}
                                                        conversationId={conversationId}
                                                        toolCallId={item.toolCallId}
                                                        onOpenApp={onOpenApp}
                                                        onOpenFile={onOpenFile}
                                                        onOpenTable={onOpenTable}
                                                    />
                                                );
                                            }
                                            if (item.kind === "plan") {
                                                return <PlanCard key={item.id} steps={item.steps} />;
                                            }
                                            if (item.kind === "tool-card") {
                                                return (
                                                    <ToolCardView
                                                        key={item.id}
                                                        card={item.card}
                                                        conversationId={conversationId}
                                                        toolCallId={item.toolCallId}
                                                    />
                                                );
                                            }
                                            if (item.interaction.id === dockedId) return null;
                                            return (
                                                <div key={item.id} id={"pause-" + item.interaction.id}>
                                                    <InteractionCard
                                                        interaction={item.interaction}
                                                        teammate={teammate.name}
                                                        onResolve={onResolve}
                                                    />
                                                </div>
                                            );
                                        })}

                                        {merging && streaming.text && (
                                            <div className="said">
                                                <Prose text={streaming.text} />
                                            </div>
                                        )}
                                    </Reply>
                                )}
                            </div>
                        );
                    })}

                    {live && mergeInto < 0 && streaming && (
                        <Reply seed={podId} teammate={teammate}>
                            <Notes
                                notes={liveNote(streaming)}
                                live={!streaming.text}
                                podId={podId}
                                conversationId={conversationId}
                            />
                            {streaming.text && (
                                <div className="said">
                                    <Prose text={streaming.text} />
                                </div>
                            )}
                        </Reply>
                    )}
                </div>

                {state === "running" && !streaming?.text && (
                    <div className="working">
                        <span className="working__dot" />
                        {teammate.name} is working…
                    </div>
                )}

                {state === "failed" && (
                    <div className="failed">
                        <span>{error ?? "That run failed."}</span>
                        {onRetry && (
                            <button className="btn" onClick={onRetry}>
                                Try again
                            </button>
                        )}
                    </div>
                )}

                {error && state !== "failed" && <p className="empty-row">{error}</p>}
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
                    <SendIcon size={16} />
                </button>
            )}
        </div>
    );
}
