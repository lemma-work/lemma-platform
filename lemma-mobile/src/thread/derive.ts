import { flatAskArgs } from "../data/wire";
import { pendingQueued, withPending, type PendingSend } from "@web/thread/pending-sends";
import { splitQueued, type Queued } from "@web/thread/queued";
import { keepUnchanged } from "@web/thread/turn-identity";
import { buildTurns, openInteraction, openSignIn, withRuntimeNotes, type Interaction, type OpenSignIn, type RawMessage, type RuntimeNote, type Turn } from "@web/thread/turns";

/** What a chat shows, from what it holds: the web's `LiveConversation`
 *  render rules, out of the component so the live chat and the sample one
 *  read the same, and so the tests can read them. Pure. */

export type RunState = "idle" | "running" | "waiting" | "failed";

/** The conversation's status, as the thread draws it. */
export function stateOf(status?: string | null): RunState {
    const normal = (status ?? "").toUpperCase();
    if (normal === "RUNNING" || normal === "STOP_REQUESTED") return "running";
    if (normal === "WAITING") return "waiting";
    if (normal === "FAILED") return "failed";
    return "idle";
}

/** An `ask_user` call is stored flat or wrapped; the cards read it flat. */
function flattened(messages: readonly RawMessage[]): RawMessage[] {
    let changed = false;
    const out = messages.map((message) => {
        if (message.tool_name !== "ask_user") return message;
        const flat = flatAskArgs(message.tool_args);
        if (flat === message.tool_args) return message;
        changed = true;
        return { ...message, tool_args: flat };
    });
    return changed ? out : (messages as RawMessage[]);
}

export interface Thread {
    turns: Turn[];
    /** What was said while the run worked and it has not heard yet. */
    queued: Queued[];
    /** The pause the run is blocked on, docked above the composer. */
    waitingOn: Interaction | null;
    /** A sign-in the run is stopped on, docked the same way. */
    signingIn: OpenSignIn | null;
}

/** The transcript and the waiting tray, from the messages held, the sends not
 *  yet answered, and what the runtime said. `previous` is the turns already
 *  on screen: every turn that did not change keeps its object, so its row is
 *  not drawn again when a message lands elsewhere. */
export function threadOf(input: {
    messages: readonly RawMessage[];
    pending: readonly PendingSend[];
    running: boolean;
    withdrawn: ReadonlySet<string>;
    notes: readonly RuntimeNote[];
    previous: readonly Turn[];
}): Thread {
    const held = flattened(input.messages);
    const split = splitQueued(withPending(held, input.pending), input.running, input.withdrawn);
    const going = pendingQueued(input.pending, held);
    const queued = going.length === 0 ? split.queued : [...split.queued, ...going];
    const turns = keepUnchanged(input.previous, withRuntimeNotes(buildTurns(split.transcript), input.notes));
    return { turns, queued, waitingOn: openInteraction(turns), signingIn: openSignIn(turns) };
}

/** The line under the composer: what the conversation is waiting on, said
 *  only when it is not already the card docked right above it. */
export function composerNote(input: {
    running: boolean;
    winding: boolean;
    state: RunState;
    waitingOn: Interaction | null;
    signingIn: OpenSignIn | null;
}): string | undefined {
    if (input.winding && input.running) return "stopping…";
    if (input.waitingOn?.kind === "approval") return "sending a message skips this request";
    if (input.waitingOn || input.signingIn) return undefined;
    if (input.state === "waiting") return "waiting on you";
    return undefined;
}

/** When the messages in the tray will be heard. */
export function queuedNote(queued: readonly Queued[], name: string): string | undefined {
    if (queued.length === 0) return undefined;
    const these = queued.length === 1 ? "this" : "these";
    return queued.some((one) => one.waitingOnAnswer)
        ? "Waiting on your answer: " + name + " hears " + these + " once you reply to its question"
        : name + " hears " + these + " as soon as the work in progress allows";
}

/** What a run says it is doing before it says anything, held as a constant
 *  so the turn holding it is not redrawn for a new empty one every render. */
export const NOTHING_YET = { text: "", thinking: "", tool: null } as const;

/** Which turn a run in flight is drawn into: the last, from the moment the
 *  message goes, so "Working" and then the reply are one element, not three
 *  in a row. -1 when there is no turn yet to merge into. */
export function mergeIndex(turns: readonly Turn[], live: boolean, working: boolean): number {
    return (live || working) && turns.length > 0 ? turns.length - 1 : -1;
}
