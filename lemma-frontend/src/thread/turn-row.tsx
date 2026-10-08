import { CheckIcon, ChevronDownIcon, ChevronRightIcon, LockIcon, MemoryIcon } from "@/ui/icons";
import { memo, useState, type ReactNode } from "react";
import { TRANSCRIPT_ROW_ATTRIBUTE } from "./use-transcript-scroll";
import { Prose, StreamingProse } from "./markdown";
import { ClampedProse } from "./clamped-prose";
import { CopyButton } from "./copy-button";
import { Mark } from "@/shell/mark";
import { writeAddress } from "@/shell/address";
import { ResourceCard } from "./resource-card";
import { PlanCard } from "./plan-card";
import { ToolCardView } from "./tool-card-view";
import { InteractionCard, type Resolve } from "./interaction-card";
import { liveNote, spanOf, type HumanMessage, type Note, type Streaming, type Turn } from "./turns";
import type { Persona } from "@/data";
import type { Noted } from "./memory-notes";

/** The turn's work, as one line.
 *
 *  A finished run says what it cost — "Worked for 2m 14s · 7 steps" — because
 *  that is the question a person actually has about a reply that took a while,
 *  and answering it in the closed state is what makes the closed state
 *  acceptable. A live run says what it is doing right now instead; the duration
 *  is not interesting yet, and a ticking clock over a working agent reads as a
 *  countdown. */
export function Notes({
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
    /* A live run with no step yet still says it is working, in the same
       place and the same shape its first step will take — the message has
       just gone, and this is the teammate taking it. Nothing to open yet. */
    if (notes.length === 0 && !live) return null;
    const empty = notes.length === 0;
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
     *  Finished: nothing. Not the last comment — a run of forty-six steps
     *  whose last comment came at step twelve would present that sentence as
     *  a summary of the whole thing, which is a claim nobody made. And not the
     *  last step's label, which it used to be: that is usually "Thought", and
     *  "Worked for 1m 25s · 18 steps | Thought" reads as two controls, the
     *  second saying nothing. What a finished run owes you is what it cost,
     *  and the count says that on its own. */
    const saying = live ? [...notes].reverse().find((note) => note.said && note.detail) : undefined;
    const peek = live ? (saying ? saying.detail : last?.label) : null;

    return (
        <div className="steps" data-live={live ? "" : undefined}>
            <button
                className="steps__toggle"
                onClick={empty ? undefined : () => setOpen((was) => !was)}
                aria-expanded={empty ? undefined : open}
                data-empty={empty ? "" : undefined}
            >
                <span className="steps__state">{live ? <span className="steps__pulse" /> : <CheckIcon size={11} />}</span>
                <span className="steps__what">{live ? "Working" : span ? "Worked for " + span + " · " + count : count}</span>
                {!open && peek && <span className="steps__peek">{peek}</span>}
                {!empty && <span className="steps__chev">{open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}</span>}
            </button>
            {open && (
                <ol className="steps__list">
                    {notes.map((note, index) => (
                        <li
                            key={index}
                            data-kind={note.kind}
                            data-card={note.card ? "" : undefined}
                            data-nested={note.nested ? "" : undefined}
                        >
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

export function Reply({ teammate, seed, at, children }: { seed: string; teammate: Persona; at?: string; children: ReactNode }) {
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

/** A message another teammate put here: its answer to a request of yours, or a
 *  request of its own. Either way it is input, not yours, so it is drawn under
 *  that teammate's name with a chip saying which, and -- where the other side
 *  is the reader's own conversation -- a way into it. A request it made on its
 *  own, over its connection, links nowhere: where it asked from belongs to
 *  whoever started that run, not necessarily the person reading. */
function relayed(message: HumanMessage): { name: string; chip: string; href: string | null } | null {
    const at = (podId: string, conversationId: string) =>
        podId && conversationId ? writeAddress({ podId, tabId: "conversation", conversationId, agentName: null }) : null;
    const answered = message.answeredBy;
    if (answered) {
        return { name: answered.name, chip: "Answered your request", href: at(answered.podId, answered.conversationId) };
    }
    const asked = message.askedBy;
    if (!asked) return null;
    return asked.forYou
        ? { name: asked.name, chip: "Asked for you", href: at(asked.podId, asked.conversationId) }
        : { name: asked.name, chip: "Asked on its own", href: null };
}

/** A person's message. Yours sits on the far side, marked when it was a note
 *  to the bot alone. In a conversation where people outside the space ask,
 *  what came in from the group is theirs, not yours: it takes the near side,
 *  their name, and a chip saying they are not in the space.
 *
 *  One still on its way says so where its time would be, and one the server
 *  refused stays where it was written with what can be done about it — the
 *  text is not silently put back in the composer, where it reads as never
 *  having been sent at all. */
function Human({
    message,
    bot,
    outsiders,
    onRetry,
    onEdit,
}: {
    message: HumanMessage;
    bot: string;
    outsiders?: string;
    onRetry?: (id: string) => void;
    onEdit?: (id: string) => void;
}) {
    const teammate = relayed(message);
    const guest = Boolean(outsiders && message.from) || Boolean(teammate);
    const at = message.pending === "sending" ? "Sending…" : message.pending === "failed" ? "Not sent" : message.at;
    return (
        <div className={"msg msg--you" + (guest ? " msg--guest" : "")} data-pending={message.pending}>
            <div className="msg__head">
                <span className="msg__who">{teammate ? teammate.name : guest ? message.from : "You"}</span>
                {teammate && (teammate.href
                    ? <a className="msg__chip" href={teammate.href}>{teammate.chip}</a>
                    : <span className="msg__chip">{teammate.chip}</span>)}
                {guest && !teammate && <span className="msg__chip">Not in {outsiders}</span>}
                <span className="msg__at">{at}</span>
            </div>
            {message.note && (
                <span className="msg__chip msg__chip--note"><LockIcon size={11} aria-hidden="true" /> Note to {bot}</span>
            )}
            <div className="msg__body">
                <Prose text={message.text} />
                <div className="message-actions">
                    <CopyButton text={message.text} label="Copy message" />
                </div>
            </div>
            {message.pending === "failed" && (onRetry || onEdit) && (
                <div className="msg__unsent">
                    {onRetry && <button className="linkish" onClick={() => onRetry(message.id)}>Retry</button>}
                    {onEdit && <button className="linkish" onClick={() => onEdit(message.id)}>Edit</button>}
                </div>
            )}
        </div>
    );
}

/** "Kit noted this · Pricing". The teammate writes its notes silently; this is
 *  the platform saying that it did, from the write itself, so it appears
 *  whether or not the reply mentions it. Each topic opens the note. */
function NotedLine({ teammate, noted, onOpenFile }: { teammate: string; noted: Noted[]; onOpenFile?: (path: string) => void }) {
    const mine = noted.every((one) => one.private);
    return (
        <p className="noted">
            <MemoryIcon size={13} aria-hidden="true" />
            <span>{teammate} noted this{mine ? " for you" : ""}</span>
            {noted.map((one) => (
                <span key={one.path} className="noted__topic">
                    {" · "}
                    {onOpenFile
                        ? <button className="linkish" onClick={() => onOpenFile(one.path)}>{one.topic}</button>
                        : one.topic}
                </span>
            ))}
        </p>
    );
}

/** One turn: what was asked, the work, and what came back.
 *
 *  Memoised, and that is most of why it is its own component. The transcript
 *  re-renders for every few tokens of the reply being written; drawn inline,
 *  that was every turn of the conversation drawn again — each answer's markdown
 *  parsed again — for every one of them. Turns keep their identity across
 *  renders (`turn-identity.ts`) and every handler handed in here is stable, so
 *  only the turn the stream is merging into redraws. */
export const TurnRow = memo(function TurnRow({
    turn,
    streaming,
    teammate,
    seed,
    podId,
    conversationId,
    outsiders,
    dockedId,
    runEnded,
    onOpenApp,
    onOpenFile,
    onOpenTable,
    onResolve,
    onRetrySend,
    onEditSend,
}: {
    turn: Turn;
    /** The run in flight, handed to the one turn it is merging into. */
    streaming: Streaming | null;
    teammate: Persona;
    seed: string;
    podId: string;
    conversationId?: string | null;
    outsiders?: string;
    dockedId?: string | null;
    runEnded: boolean;
    onOpenApp?: (name: string) => void;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
    onResolve?: Resolve;
    onRetrySend?: (id: string) => void;
    onEditSend?: (id: string) => void;
}) {
    const merging = Boolean(streaming);
    const notes = streaming ? [...turn.notes, ...liveNote(streaming, turn.notes)] : turn.notes;
    const spoke = turn.items.find((item) => item.kind === "text");

    return (
        <div {...{ [TRANSCRIPT_ROW_ATTRIBUTE]: "" }}>
            {turn.day && <div className="day">{turn.day}</div>}

            {turn.notice && <div className="notice"><ClampedProse text={turn.notice} /></div>}

            {/* Attribution above the surface, the same as a
                reply — a name tucked *inside* the block made
                "cool cool" two lines tall and left the two
                speakers built differently for no reason. */}
            {turn.human && (
                <Human message={turn.human} bot={teammate.name} outsiders={outsiders} onRetry={onRetrySend} onEdit={onEditSend} />
            )}

            {(notes.length > 0 || turn.items.length > 0 || merging) && (
                <Reply
                    seed={seed}
                    teammate={teammate}
                    at={spoke?.kind === "text" ? spoke.at : undefined}
                >
                    <Notes
                        notes={notes}
                        live={merging && !streaming?.text}
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
                                <div className="message-text" key={item.id}>
                                    <div className="said"><Prose text={item.text} /></div>
                                    <div className="message-actions">
                                        <CopyButton text={item.text} label="Copy message" />
                                    </div>
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
                            /* An open sign-in is on the shelf above the composer. */
                            if (item.id === dockedId) return null;
                            return (
                                <ToolCardView
                                    key={item.id}
                                    card={item.card}
                                    podId={podId}
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
                                    runEnded={runEnded}
                                />
                            </div>
                        );
                    })}

                    {streaming?.text && (
                        <div className="said">
                            <StreamingProse text={streaming.text} />
                        </div>
                    )}

                    {turn.noted && turn.noted.length > 0 && (
                        <NotedLine teammate={teammate.name} noted={turn.noted} onOpenFile={onOpenFile} />
                    )}
                </Reply>
            )}
        </div>
    );
});
