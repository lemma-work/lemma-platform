import type { RawMessage } from "./turns";

/** Messages sent while the teammate was working, and where to draw them.
 *
 *  A message typed during a run joins that run, which loaded its history before
 *  the message existed. Until something delivers it -- the in-process harness
 *  at its next step, a local coding agent that can be steered within a second
 *  or two, or otherwise the follow-up turn once this one ends -- the teammate
 *  has not seen it. The server says which is which in the message's metadata
 *  (`lemma-backend/app/modules/agent/domain/queued_messages.py`), and the claim
 *  arrives on the live stream as the same message with that metadata updated.
 *
 *  Drawn inline at its sequence, a queued message split the running turn in
 *  two: everything the teammate said after it -- still about the *previous*
 *  message -- landed under it, as though it were the answer. So while it waits
 *  it sits above the composer instead, labelled as waiting, where it can be
 *  taken back; and once delivered it joins the transcript where the teammate
 *  actually took it in. */

export interface Queued {
    id: string;
    text: string;
    /** Still nobody's to deliver, so the person can take it back. False once
     *  it is on its way to a local agent's turn: it may already be in the
     *  agent's context, and withdrawing it would hide what the agent is
     *  answering. */
    withdrawable: boolean;
}

function meta(message: RawMessage): Record<string, unknown> {
    return message.metadata ?? {};
}

/** Said while the teammate was working, and not yet delivered to it. */
export function isQueued(message: RawMessage): boolean {
    if (message.role !== "user") return false;
    const metadata = meta(message);
    return metadata.during_active_run === true && !metadata.steered_into_run;
}

export function isWithdrawable(message: RawMessage): boolean {
    if (!isQueued(message)) return false;
    const metadata = meta(message);
    return !metadata.steer_dispatched_at || Boolean(metadata.steer_undelivered);
}

/** Split what the session holds into the transcript and the waiting tray.
 *
 *  Only while a run is going. A run that ended without delivering them -- the
 *  person pressed Stop -- has nothing left to wait for, and a tray promising
 *  otherwise would be a promise nothing keeps; they are then drawn as what they
 *  are, messages that were sent. `withdrawn` hides the ones taken back here
 *  before the server's list catches up. */
export function splitQueued(
    messages: RawMessage[],
    running: boolean,
    withdrawn: ReadonlySet<string> = new Set(),
): { transcript: RawMessage[]; queued: Queued[] } {
    const transcript: RawMessage[] = [];
    const queued: Queued[] = [];
    const ordered = [...messages].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
    for (const message of ordered) {
        if (message.id && withdrawn.has(message.id)) continue;
        if (running && isQueued(message)) {
            queued.push({
                id: message.id ?? "",
                text: (message.text ?? message.content ?? "").trim(),
                withdrawable: isWithdrawable(message),
            });
            continue;
        }
        transcript.push(message);
    }
    return { transcript: inDeliveryOrder(transcript), queued };
}

/** The transcript as the teammate heard it.
 *
 *  A message is stored at the point it was sent, but a follow-up turn takes in
 *  everything queued behind the previous one only once that one has finished.
 *  Left at its sequence, the previous turn's closing answer read as the reply
 *  to it. Placed just ahead of the run that delivered it, it reads the way the
 *  conversation actually went. A message steered into the run that was already
 *  going stays where it is: that is where the agent heard it.
 *
 *  Done by giving the moved messages a display sequence rather than by
 *  reordering, because `buildTurns` orders by sequence and is right to. The
 *  stored message is untouched. */
export function inDeliveryOrder(messages: RawMessage[]): RawMessage[] {
    const moved = new Map<string, RawMessage[]>();
    const rest: RawMessage[] = [];
    for (const message of messages) {
        const into = meta(message).steered_into_run;
        if (message.role === "user" && typeof into === "string" && into && into !== message.agent_run_id) {
            moved.set(into, [...(moved.get(into) ?? []), message]);
        } else {
            rest.push(message);
        }
    }
    if (moved.size === 0) return messages;
    const last = Math.max(0, ...messages.map((message) => message.sequence ?? 0));
    const placed: RawMessage[] = [];
    for (const [run, waiting] of moved) {
        const starts = rest
            .filter((message) => message.agent_run_id === run)
            .map((message) => message.sequence ?? 0);
        /* The run that claimed them has said nothing yet: they are the newest
           thing in the conversation. */
        const before = starts.length > 0 ? Math.min(...starts) : last + 1;
        const ordered = [...waiting].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
        ordered.forEach((message, index) => {
            placed.push({ ...message, sequence: before - 1 + (index + 1) / (ordered.length + 1) });
        });
    }
    return [...rest, ...placed].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
}

/** Which of Stop and Send the composer offers.
 *
 *  Stop used to take Send's place for as long as a run was going, so the only
 *  thing a person could do to a teammate heading the wrong way was throw its
 *  work away. Now Stop sits beside Send, and Send appears the moment there is
 *  something to send. */
export function composerActions(running: boolean, hasSomethingToSend: boolean): { stop: boolean; send: boolean } {
    return { stop: running, send: !running || hasSomethingToSend };
}
