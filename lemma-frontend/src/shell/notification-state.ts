/** What one notification is asking of the person, decided once.
 *
 *  Two states are carried independently and they are about different things.
 *  `status` is about the person — has the thing we needed from them happened.
 *  `delivery_status` is about the channel — could any mailbox or chat app carry
 *  it. `UNDELIVERABLE` is not a failure to handle: nothing could carry it, it is
 *  in the inbox regardless, and that is the whole point of there being an inbox.
 */

export interface Notification {
    id: string;
    title: string;
    body: string;
    status: string;
    delivery_status?: string;
    undeliverable_reason?: string | null;
    expects_response: boolean;
    awaiting_response: boolean;
    responds_through_action: boolean;
    /** `{run_id, node_id}` when this is answered by a workflow form. */
    action?: Record<string, unknown> | null;
    read_at?: string | null;
    responded_at?: string | null;
    response_summary?: string | null;
    created_at: string;
    origin_conversation_id?: string | null;
}

/** What this row can offer.
 *
 *  - `answer`     — it wants words, and they can be typed here.
 *  - `form`       — it wants a form, and the ask names the run and node to
 *                   submit it against, so it can be drawn and sent from here.
 *  - `elsewhere`  — it wants a form but did not say which run or node, so
 *                   there is nothing to submit against and the platform's own
 *                   page is the honest answer.
 *  - `acknowledge`— it asked for nothing; the only move is to dismiss it.
 *  - `done`       — it has been answered or is over.
 */
export type NotificationMove = "answer" | "form" | "elsewhere" | "acknowledge" | "done";

export function moveFor(notification: Notification): NotificationMove {
    if (notification.status !== "OPEN") return "done";
    if (!notification.awaiting_response) return "acknowledge";
    if (!notification.responds_through_action) return "answer";
    /* The form can only be drawn here when the notification says which run and
       which node it belongs to. Without both there is nothing to submit
       against, and the platform's own page is the honest fallback rather than
       a form that would 422. */
    return formTarget(notification) ? "form" : "elsewhere";
}

/** The run and node this ask is answered against.
 *
 *  Read off the notification and never taken from anywhere else — the backend
 *  is deliberate about this: the run and node belong to whoever asked, and
 *  letting a recipient name them would let somebody submit against any run
 *  whose ids they could guess.
 */
export function formTarget(notification: Notification): { runId: string; nodeId: string } | null {
    const action = notification.action ?? {};
    const runId = action["run_id"];
    const nodeId = action["node_id"];
    if (typeof runId !== "string" || !runId) return null;
    if (typeof nodeId !== "string" || !nodeId) return null;
    return { runId, nodeId };
}

/** The schema a run is waiting on, if it is still waiting on this node.
 *
 *  The node has to match. A run moves on, and a form submitted against a node
 *  it has already passed is a 422 — so a stale notification should say the work
 *  moved rather than draw a form that cannot be sent.
 */
/** A run carrying a wait, as much of it as these two readers need. */
type WaitingRun = {
    active_wait?: {
        node_id?: string;
        payload?: { input_schema?: unknown; ui_schema?: unknown } | null;
    } | null;
} | null | undefined;

/** The payload of the wait this node is stopped on, or nothing.
 *
 *  Shared by both readers so the node check cannot drift between them. Taking
 *  a schema from one wait and an order from another would render a form
 *  nobody authored, and would do it silently.
 */
function waitingPayload(run: WaitingRun, nodeId: string): Record<string, unknown> | null {
    const wait = run?.active_wait;
    if (!wait || wait.node_id !== nodeId) return null;
    const payload = wait.payload;
    return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
}

function plainObject(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
}

export function waitingSchema(run: WaitingRun, nodeId: string): Record<string, unknown> | null {
    return plainObject(waitingPayload(run, nodeId)?.input_schema);
}

/** How the author meant the form to read.
 *
 *  Not decoration. With no `ui:order` the SDK sorts the fields
 *  **alphabetically by label** (`schema-form.js`), so a form authored
 *  `terms_ok`, `payment_days`, `contact_email` was being answered as Contact,
 *  Payment days, The terms are acceptable — an order nobody chose, in a form
 *  whose whole job is to be answered correctly.
 *
 *  It was on the wire the entire time: the form node resolves `input_schema`
 *  and `ui_schema` together and stores both on the wait
 *  (`execution/executors/form.py:45-51`), so reading one and dropping the
 *  other threw the author's order away for no saving at all.
 */
export function waitingUiSchema(run: WaitingRun, nodeId: string): Record<string, unknown> | null {
    return plainObject(waitingPayload(run, nodeId)?.ui_schema);
}

/** Why this never reached a chat app or a mailbox, said to a person.
 *
 *  Null when it was delivered, because saying so is noise — the interesting
 *  case is the one where a person is reading this *because* nothing else
 *  reached them.
 */
export function deliveryNote(notification: Notification): string | null {
    if (notification.delivery_status === "UNDELIVERABLE") {
        return notification.undeliverable_reason?.trim() || "No channel could carry this, so it is here instead.";
    }
    if (notification.delivery_status === "FAILED") {
        return notification.undeliverable_reason?.trim() || "Sending this somewhere else failed, so it is here instead.";
    }
    return null;
}

/** Unread is about being read, not about being finished.
 *
 *  Keyed on `read_at` alone, matching the server's own count. A badge that only
 *  clears once the work is done is a badge people stop looking at.
 */
export function isUnread(notification: Notification): boolean {
    return !notification.read_at;
}

/** How a count reads on a badge. Past a point the number stops being the
 *  information and "a lot" is. */
export function badgeCount(count: number): string | null {
    if (count <= 0) return null;
    return count > 99 ? "99+" : String(count);
}

/** What the row says has already happened, if anything. */
export function outcomeOf(notification: Notification): string | null {
    if (notification.status === "RESPONDED") {
        return notification.response_summary?.trim() ? "You said: " + notification.response_summary.trim() : "Answered";
    }
    if (notification.status === "ACKNOWLEDGED") return "Dismissed";
    if (notification.status === "EXPIRED") return "Expired before it was answered";
    if (notification.status === "CANCELLED") return "Withdrawn";
    return null;
}

/** The two ways answering can be refused, in words rather than as a status code.
 *
 *  Both are real product states rather than errors: somebody else got there
 *  first, or the thing is answered by completing its own form instead.
 */
export function conflictNote(notification: Notification): string {
    return notification.responds_through_action
        ? "This one is answered by completing its form, not by replying here."
        : "Somebody has already answered this.";
}
