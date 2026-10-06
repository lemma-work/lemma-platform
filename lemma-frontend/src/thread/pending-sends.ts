import type { Queued } from "./queued";
import type { RawMessage } from "./turns";

/** Messages on their way, drawn before the server has them.
 *
 *  A message used to appear only when the server sent it back on the run's
 *  stream — after the conversation was created, the message stored and the run
 *  started. Until then the text had left the composer and was nowhere, which is
 *  most of why sending felt slow. Now it is drawn the moment Enter is pressed,
 *  and the copy here stands in until the server's own arrives.
 *
 *  The two are paired by an id this app mints and sends in the message's
 *  metadata, which the server stores as given and sends back with the message.
 *  Text alone cannot pair them: two sends of "yes" are two messages, and a
 *  message carrying attachments comes back with their references appended. Text
 *  is still the fallback, for a server that does not echo the id, but only
 *  against messages that were not there when the send began. */

/** The id riding in the message's metadata. Stored by the server untouched;
 *  nothing reads it there. */
export const CLIENT_MESSAGE_KEY = "client_message_id";

/** Marks the stand-in, never sent: what the turn reads to say "Sending…". */
const PENDING_KEY = "lemma_pending";

export type PendingState = "sending" | "failed";

export interface PendingSend {
    clientId: string;
    text: string;
    /** `send` opens a turn of its own. `steer` joins the run in flight, and
     *  waits in the tray above the composer like any message the run has not
     *  heard yet (see `queued.ts`). */
    kind: "send" | "steer";
    state: PendingState;
    at: string;
    /** Every user message the conversation held when this one was sent. Only
     *  a message outside it can be this one's echo when pairing falls back to
     *  text. */
    before: ReadonlySet<string>;
}

export function newClientId(): string {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

export function clientIdOf(message: RawMessage): string | undefined {
    const id = message.metadata?.[CLIENT_MESSAGE_KEY];
    return typeof id === "string" && id ? id : undefined;
}

/** Whether a message is a stand-in, and how its send is going. */
export function pendingStateOf(message: RawMessage): PendingState | undefined {
    const state = message.metadata?.[PENDING_KEY];
    return state === "sending" || state === "failed" ? state : undefined;
}

export function userMessageIds(messages: readonly RawMessage[]): ReadonlySet<string> {
    const ids = new Set<string>();
    for (const message of messages) if (message.role === "user" && message.id) ids.add(message.id);
    return ids;
}

/** A new pending send, stamped now. */
export function pendingSend(
    text: string,
    kind: PendingSend["kind"],
    messages: readonly RawMessage[],
    clientId: string = newClientId(),
): PendingSend {
    return { clientId, text, kind, state: "sending", at: new Date().toISOString(), before: userMessageIds(messages) };
}

function textOf(message: RawMessage): string {
    return (message.text ?? message.content ?? "").trim();
}

/** Whether the server's copy of this send has arrived. */
export function hasArrived(pending: PendingSend, messages: readonly RawMessage[]): boolean {
    const said = pending.text.trim();
    for (const message of messages) {
        if (message.role !== "user") continue;
        const id = clientIdOf(message);
        if (id) {
            if (id === pending.clientId) return true;
            continue;
        }
        /* No id came back: a server that does not echo metadata. Only a new
           message can be this one, and one that starts with what was typed —
           attachments append their references to the end. */
        if (message.id && pending.before.has(message.id)) continue;
        if (said && textOf(message).startsWith(said)) return true;
    }
    return false;
}

/** The pending sends whose server copy has not arrived. Returns the list it
 *  was given when nothing arrived, so pruning in an effect does not loop. */
export function stillPending(pending: readonly PendingSend[], messages: readonly RawMessage[]): readonly PendingSend[] {
    if (pending.length === 0) return pending;
    const left = pending.filter((one) => !hasArrived(one, messages));
    return left.length === pending.length ? pending : left;
}

/** The conversation with its stand-ins appended, after everything the server
 *  holds. Returns the list it was given when there are none, so the turns built
 *  from it keep their identity. */
export function withPending(messages: RawMessage[], pending: readonly PendingSend[]): RawMessage[] {
    const drawn = pending.filter((one) => one.kind === "send" && !hasArrived(one, messages));
    if (drawn.length === 0) return messages;
    const last = messages.reduce((top, message) => Math.max(top, message.sequence ?? 0), 0);
    return [
        ...messages,
        ...drawn.map((one, index): RawMessage => ({
            id: one.clientId,
            role: "user",
            kind: "TEXT",
            text: one.text,
            created_at: one.at,
            sequence: last + 1 + index,
            metadata: { [CLIENT_MESSAGE_KEY]: one.clientId, [PENDING_KEY]: one.state },
        })),
    ];
}

/** Steers on their way, for the tray. Not withdrawable: there is nothing on
 *  the server to take back yet. */
export function pendingQueued(pending: readonly PendingSend[], messages: readonly RawMessage[]): Queued[] {
    return pending
        .filter((one) => one.kind === "steer" && !hasArrived(one, messages))
        .map((one) => ({ id: one.clientId, text: one.text, withdrawable: false }));
}

/** Whether a send that has not been answered by the server yet is still going
 *  — what lets the transcript say the teammate is on it before it is. */
export function isSending(pending: readonly PendingSend[]): boolean {
    return pending.some((one) => one.kind === "send" && one.state === "sending");
}
