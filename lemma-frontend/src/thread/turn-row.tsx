import { ChevronDownIcon, ChevronRightIcon, LockIcon, MemoryIcon, TerminalIcon } from "@/ui/icons";
import { memo, useEffect, useState, type ReactNode } from "react";
import { TRANSCRIPT_ROW_ATTRIBUTE } from "./use-transcript-scroll";
import { Prose, StreamingProse } from "./markdown";
import { ClampedProse } from "./clamped-prose";
import { CopyButton } from "./copy-button";
import { Mark } from "@/shell/mark";
import { writeAddress } from "@/shell/address";
import { PlaceLink } from "@/shell/place-link";
import { ResourceCard } from "./resource-card";
import { PlanCard } from "./plan-card";
import { ToolCardView } from "./tool-card-view";
import { InteractionCard, type Resolve } from "./interaction-card";
import { answerOf, foldsAway, liveNote, relayedBy, spanOf, type HumanMessage, type Note, type Streaming, type Turn, type TurnItem } from "./turns";
import { stepLine, stepNow, stepsSaid } from "./step-groups";
import type { Persona } from "@/data";
import type { Noted } from "./memory-notes";

/** The steps themselves, as opening a run of them shows them: a line each,
 *  in the order they happened. */
function StepList({ notes, podId, conversationId }: { notes: readonly Note[]; podId: string; conversationId?: string | null }) {
    return (
        <ol className="steps__list">
            {notes.map((note, index) => (
                <li key={index} data-kind={note.kind} data-nested={note.nested ? "" : undefined}>
                    <StepRow note={note} podId={podId} conversationId={conversationId} />
                </li>
            ))}
        </ol>
    );
}

/** Whether a step has more than its line: a card, or a thought. A call this
 *  app does not read has its line, and the whole of it on hover. */
function opensOnto(note: Note): boolean {
    return Boolean(note.card) || (note.kind === "thought" && note.detail.trim() !== "");
}

/** A step read rather than summarised: the command and what it printed, the
 *  pages behind an answer, the thought in full. */
function StepMore({ note, podId, conversationId }: { note: Note; podId: string; conversationId?: string | null }) {
    if (note.card) return <div className="steprow__more"><ToolCardView card={note.card} podId={podId} conversationId={conversationId} /></div>;
    return <p className={"steprow__more steps__detail" + (note.said ? " steps__detail--said" : "")}>{note.detail}</p>;
}

/** One step, on one line -- what it did and what on -- which opens onto the
 *  step itself. Open, every card and every thought at once was the whole
 *  run's output down the reply. */
function StepRow({ note, podId, conversationId }: { note: Note; podId: string; conversationId?: string | null }) {
    const [open, setOpen] = useState(false);
    const { what, on } = stepLine(note);
    const more = opensOnto(note);
    const line = (
        <>
            <span className="steprow__what">{what}</span>
            {on && <span className="steprow__on">{on}</span>}
            {more && <span className="stepgroup__chev">{open ? <ChevronDownIcon size={11} /> : <ChevronRightIcon size={11} />}</span>}
        </>
    );
    if (!more) return <span className="steprow" title={on ? what + " " + on : what}>{line}</span>;
    return (
        <>
            <button type="button" className="steprow steprow--opens" aria-expanded={open} onClick={() => setOpen((was) => !was)}>{line}</button>
            {open && <StepMore note={note} podId={podId} conversationId={conversationId} />}
        </>
    );
}

/** A run of steps between two things the agent said, as one quiet line
 *  saying what they did -- "Ran 4 commands", "Read a file, searched the web"
 *  -- over the steps themselves. In the reply where it happened, so the
 *  reply reads in order: what it said, what it did next, what it said then.
 *
 *  Closed, live or not: the line is the step summary, and the reader opens
 *  what they want to look into. One step opens straight onto itself rather
 *  than onto a list of one.
 *
 *  `podId` and `conversationId` go to the cards inside: only the image card
 *  uses them, when somebody asks it to fetch -- a `view_image` names a file in
 *  one of two stores, and neither can be read without knowing the pod, or,
 *  for a relative workspace path, the conversation it resolves against. */
export function StepGroup({ notes, podId, conversationId }: { notes: readonly Note[]; podId: string; conversationId?: string | null }) {
    const [open, setOpen] = useState(false);
    const said = stepsSaid(notes);
    if (!said) return null;
    const only = notes.length === 1 && opensOnto(notes[0]) ? notes[0] : null;
    return (
        <div className="stepgroup">
            <button type="button" className="stepgroup__toggle" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
                <span className="stepgroup__what">{said}</span>
                <span className="stepgroup__chev">{open ? <ChevronDownIcon size={11} /> : <ChevronRightIcon size={11} />}</span>
            </button>
            {open && (only
                ? <StepMore note={only} podId={podId} conversationId={conversationId} />
                : <StepList notes={notes} podId={podId} conversationId={conversationId} />)}
        </div>
    );
}

/** The line under a reply while its run is going: what it is doing now, in
 *  the present tense -- "Searching the web", or the agent's own sentence for
 *  the step in hand -- and how long the run has taken so far, so a long one
 *  is visibly still moving rather than stuck.
 *
 *  It is also the run of steps still going, which open from it as a finished
 *  run's open from its line. Drawn as a line of its own above, the same work
 *  read as both done and doing. */
export function WorkingLine({ steps = [], inFlight, since, podId = "", conversationId }: {
    /** The run's steps since it last said or showed something, landed. */
    steps?: readonly Note[];
    /** The step the stream says is under way and has not landed yet. */
    inFlight: readonly Note[];
    since?: number;
    podId?: string;
    conversationId?: string | null;
}) {
    const [started] = useState(() => since ?? Date.now());
    const [now, setNow] = useState(() => Date.now());
    const [open, setOpen] = useState(false);
    useEffect(() => {
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, []);
    const what = (
        <>
            <span className="working__dot" aria-hidden="true" />
            <span className="working__what">{stepNow([...steps, ...inFlight])}</span>
            <span className="working__time">{spanOf(started, now)}</span>
        </>
    );
    if (steps.length === 0) return <p className="working" role="status">{what}</p>;
    return (
        <div className="stepgroup" role="status">
            <button type="button" className="working working--opens" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
                {what}
                <span className="stepgroup__chev">{open ? <ChevronDownIcon size={11} /> : <ChevronRightIcon size={11} />}</span>
            </button>
            {open && <StepList notes={steps} podId={podId} conversationId={conversationId} />}
        </div>
    );
}

/** The line a stale run folds to: how long it worked, or -- with no clock
 *  to go by -- what it did. */
function workedFor(turn: Turn): string {
    const span = spanOf(turn.startedAtMs, turn.endedAtMs);
    if (span) return "Worked for " + span;
    return stepsSaid(turn.notes) || "Earlier messages";
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
    const teammate = relayedBy(message);
    const guest = Boolean(outsiders && message.from) || Boolean(teammate);
    const at = message.pending === "sending" ? "Sending…" : message.pending === "failed" ? "Not sent" : message.at;
    return (
        <div className={"msg msg--you" + (guest ? " msg--guest" : "")} data-pending={message.pending}>
            <div className="msg__head">
                <span className="msg__who">{teammate ? teammate.name : guest ? message.from : "You"}</span>
                {teammate && (teammate.place
                    ? (
                        <PlaceLink
                            className="msg__chip msg__chip--link"
                            href={writeAddress({ ...teammate.place, tabId: "conversation", agentName: null })}
                            title={"Open " + teammate.name + "’s conversation"}
                        >
                            {teammate.chip}
                        </PlaceLink>
                    )
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
    stale = false,
    resumesOnAnswer,
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
    /** Its run is over and the person has said something since. */
    stale?: boolean;
    /** The conversation is waiting on its pauses; see `InteractionCard`. */
    resumesOnAnswer?: boolean;
    onOpenApp?: (name: string) => void;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
    onResolve?: Resolve;
    onRetrySend?: (id: string) => void;
    onEditSend?: (id: string) => void;
}) {
    const merging = Boolean(streaming);
    /* The step in flight, which has not landed in the transcript yet. */
    const inFlight = streaming ? liveNote(streaming, turn.notes) : [];
    const spoke = turn.items.find((item) => item.kind === "text");
    /* The run of steps the reply has not moved past yet has no line of its
       own: the working line under it says what it is doing, and a line above
       saying what it did would read the same work as done and doing. */
    const last = turn.items[turn.items.length - 1];
    const going = merging && !streaming?.text && last?.kind === "steps" ? last : null;
    const goingNotes = going ? turn.notes.slice(going.from, going.from + going.count) : [];
    /* A stale run reads as its answer, after one line for the work before
       it (`answerOf`), which opens onto the run as it happened. */
    const [unfolded, setUnfolded] = useState(false);
    const answer = stale ? answerOf(turn.items) : -1;
    const folding = answer > 0;
    const hidden = (item: TurnItem, index: number) => folding && !unfolded && index < answer && foldsAway(item);

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

            {(turn.notes.length > 0 || turn.items.length > 0 || merging || Boolean(turn.runtimeNotes?.length)) && (
                <Reply
                    seed={seed}
                    teammate={teammate}
                    at={spoke?.kind === "text" ? spoke.at : undefined}
                >
                    {/* One list, in the order it happened. A
                        question the run is blocked on must
                        not float above the widget it was
                        asking about. */}
                    {folding && (
                        <div className="stepgroup">
                            <button type="button" className="stepgroup__toggle" aria-expanded={unfolded} onClick={() => setUnfolded((was) => !was)}>
                                <span className="stepgroup__what">{workedFor(turn)}</span>
                                <span className="stepgroup__chev">{unfolded ? <ChevronDownIcon size={11} /> : <ChevronRightIcon size={11} />}</span>
                            </button>
                        </div>
                    )}
                    {turn.items.map((item, index) => {
                        if (hidden(item, index)) return null;
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
                        if (item.kind === "steps") {
                            if (item === going) return null;
                            return <StepGroup key={item.id} notes={turn.notes.slice(item.from, item.from + item.count)} podId={podId} conversationId={conversationId} />;
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
                                    resumesOnAnswer={resumesOnAnswer}
                                />
                            </div>
                        );
                    })}

                    {streaming?.text && (
                        <div className="said">
                            <StreamingProse text={streaming.text} />
                        </div>
                    )}

                    {merging && !streaming?.text && (
                        <WorkingLine steps={goingNotes} inFlight={inFlight} since={turn.startedAtMs} podId={podId} conversationId={conversationId} />
                    )}

                    {turn.noted && turn.noted.length > 0 && (
                        <NotedLine teammate={teammate.name} noted={turn.noted} onOpenFile={onOpenFile} />
                    )}

                    {/* What the agent's runtime said about the run — a model it
                        no longer offers, a session it restarted. The run went
                        on, so this is a quiet line beside the reply rather than
                        an error over it. */}
                    {turn.runtimeNotes?.map((note) => (
                        <p className="noted" role="note" key={note}>
                            <TerminalIcon size={13} />
                            <span>{note}</span>
                        </p>
                    ))}
                </Reply>
            )}
        </div>
    );
});
