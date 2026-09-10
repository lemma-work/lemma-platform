/**
 * Messages typed at an agent that cannot be told anything mid-turn.
 *
 * ACP has no steering primitive. `session/prompt` is one request that returns
 * when the turn ends, and as of 2.1.0 there is no method to add input to one in
 * flight — so a message typed at an Agent Host run cannot reach the agent until
 * its turn is over. An in-process LEMMA run is different: a capability claims
 * the message per node and hands it straight to the model.
 *
 * The old behaviour persisted the message immediately and let a follow-up run
 * answer it once the turn ended. It worked, and it looked like it had not: the
 * transcript showed the message as sent while the agent could not see it, so a
 * person reasonably concluded it had been ignored.
 *
 * These are held here instead, named as queued, until they can actually be
 * delivered — either when the turn ends, or when someone interrupts it on
 * purpose.
 *
 * Kept in `localStorage` rather than in React state alone: a queued message
 * that a page navigation silently discards is the same broken promise in a
 * different costume. Every access is guarded, because a browser told to block
 * site data throws on the accessor itself rather than returning nothing.
 */

export type QueuedSteer = {
    /** Stable across reloads, so a list can key on it and remove one. */
    id: string;
    content: string;
    queuedAt: string;
};

const PREFIX = "lemma.queued-steers.";

function keyFor(conversationId: string): string {
    return `${PREFIX}${conversationId}`;
}

function storage(): Storage | null {
    try {
        if (typeof window === "undefined" || !window.localStorage) return null;
        return window.localStorage;
    } catch {
        // Reading the property itself throws when site data is blocked.
        return null;
    }
}

/** What is queued for this conversation, oldest first. */
export function readQueuedSteers(conversationId: string): QueuedSteer[] {
    const store = storage();
    if (!store) return [];
    try {
        const raw = store.getItem(keyFor(conversationId));
        if (!raw) return [];
        const parsed: unknown = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        // Validated one by one: this is data from a previous version of the app
        // as much as from this one, and a half-written entry must not take the
        // rest of the queue with it.
        return parsed.filter(isQueuedSteer);
    } catch {
        return [];
    }
}

export function writeQueuedSteers(conversationId: string, items: QueuedSteer[]): void {
    const store = storage();
    if (!store) return;
    try {
        if (items.length === 0) store.removeItem(keyFor(conversationId));
        else store.setItem(keyFor(conversationId), JSON.stringify(items));
    } catch {
        // Out of quota, or blocked. The queue still works for this page.
    }
}

export function appendQueuedSteer(conversationId: string, content: string): QueuedSteer[] {
    const item: QueuedSteer = {
        id: newSteerId(),
        content,
        queuedAt: new Date().toISOString(),
    };
    const next = [...readQueuedSteers(conversationId), item];
    writeQueuedSteers(conversationId, next);
    return next;
}

export function removeQueuedSteer(conversationId: string, id: string): QueuedSteer[] {
    const next = readQueuedSteers(conversationId).filter((item) => item.id !== id);
    writeQueuedSteers(conversationId, next);
    return next;
}

export function clearQueuedSteers(conversationId: string): void {
    writeQueuedSteers(conversationId, []);
}

function isQueuedSteer(value: unknown): value is QueuedSteer {
    if (typeof value !== "object" || value === null) return false;
    const record = value as Record<string, unknown>;
    return (
        typeof record.id === "string"
        && record.id.length > 0
        && typeof record.content === "string"
        && record.content.length > 0
        && typeof record.queuedAt === "string"
    );
}

function newSteerId(): string {
    try {
        if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    } catch {
        // Falls through to the counter below.
    }
    return `steer-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
