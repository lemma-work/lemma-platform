import { dehydrate, hydrate, type DehydratedState, type QueryClient } from "@tanstack/react-query";
import { upstreamUrl } from "./client";
import { key } from "./storage";

/* What the app keeps on this device between visits, so it opens on what the
 * reader saw last rather than on a spinner, and the network catches up
 * behind it: the lists the frame is drawn from -- organizations, Lemmas,
 * their chats and shared chats, what waits on the reader, the names of
 * apps, tables and files. Names and titles only: a chat's title but not its
 * last line, a need's one-line title but not the question in full, what a
 * contact wrote or the answers offered (`withoutWords`). Never what is
 * inside a file, a table or a chat, a Lemma's plan included, never a
 * schedule's instruction or a form's fields, and never a change still on
 * its way to the API.
 *
 * One record in IndexedDB, written at most once a second while the cache
 * changes. It is the signed-in person's: read back only for them, on the same API and the
 * same build, within a week, and wiped wherever the session already forgets
 * what the last person could see -- signing out, or someone else signing in. */

const DB_NAME = "lemma-device-cache";
const STORE = "kept";
const RECORD = "frame";
/** Older than this, it says more about the past than the present. */
export const KEPT_FOR_MS = 7 * 86_400_000;
/** How long after a change the cache is written: one write for a burst of
 *  answers, not one per answer. */
const WRITE_AFTER_MS = 1000;
/** A device too slow to answer is one the app does not wait on. */
const READ_WITHIN_MS = 800;

/** Stamped at build (`next.config.ts`), so what a browser kept from an
 *  older build -- whose answers may be shaped differently -- is dropped
 *  rather than read as this one's. */
const BUILD = process.env.NEXT_PUBLIC_LEMMA_BUILD ?? "dev";

export interface KeptRecord {
    build: string;
    api: string;
    owner: string;
    at: number;
    state: DehydratedState;
}

/** The first words of the keys worth keeping: lists, each small, each read
 *  before anything can be drawn.
 *
 *  Not kept, though the frame reads them early: the workflow forms waiting
 *  on the reader (`workflow-waiting`), whose rows carry each form's schema
 *  with values resolved from its run, and a Lemma's schedules
 *  (`outcome-schedules`), whose rows carry each instruction and the whole
 *  row as it arrived. Their words are what they are for, so they cannot be
 *  kept without them, and a screen that opened on a blanked copy could save
 *  the blank back. */
const LISTS: ReadonlySet<string> = new Set([
    "orgs",
    "current-user",
    "pods",
    "pod-detail",
    "conversation-asks",
    "org-recents",
    "outcome-apps",
    "outcome-tables",
    "outcome-files",
    "outcome-surfaces",
]);

/** Whether a query is one the device keeps. */
export function keptOnDevice(queryKey: readonly unknown[]): boolean {
    const [head, , kind] = queryKey;
    if (typeof head !== "string") return false;
    if (LISTS.has(head)) return true;
    /* A Lemma's recent chats, as the frame reads them -- not a transcript. */
    if (head === "conversations") return kind === "outcome-activity";
    /* Its shared chats, listed -- not their lines. */
    if (head === "channel") return kind === "list";
    if (head === "workflows") return kind === "names";
    return false;
}

/** What of the cache the device keeps: the lists that have answered, and no
 *  mutations -- by default a paused one, a message sent offline, would be
 *  kept words and all.
 *
 *  A list whose last read failed is kept too, as the answer it holds: a
 *  query that fails after answering is an "error" still holding its data,
 *  and keeping only the successes would let one visit while the API is
 *  failing cost the next visit every list.
 *
 *  Every kept list comes back invalidated, so it is read again as soon as
 *  the frame shows it, whatever its `staleTime`: a reload a minute after the
 *  last write would otherwise take the kept answer as fresh and not read the
 *  Lemmas again for minutes. Its `dataUpdatedAt` stays the time it was read,
 *  which is still true and which `useRemember` relies on. */
export function keptState(client: QueryClient): DehydratedState {
    const state = dehydrate(client, {
        shouldDehydrateQuery: (query) => query.state.data !== undefined && keptOnDevice(query.queryKey),
        shouldDehydrateMutation: () => false,
    });
    return {
        ...state,
        queries: state.queries.map((query) => ({
            ...query,
            state: {
                ...query.state,
                data: withoutWords(query.queryKey, query.state.data),
                status: "success" as const,
                fetchStatus: "idle" as const,
                error: null,
                fetchFailureCount: 0,
                fetchFailureReason: null,
                isInvalidated: true,
            },
        })),
    };
}

type Row = Record<string, unknown>;
const rows = (data: unknown): Row[] | null =>
    Array.isArray(data) ? data.filter((one): one is Row => Boolean(one) && typeof one === "object") : null;

/** A chat as it goes to disk: its last line keeps who said it and when, not
 *  what; its attention keeps since when, not why. */
function chatWithoutWords(channel: Row): Row {
    const lastLine = channel.lastLine && typeof channel.lastLine === "object" ? { ...(channel.lastLine as Row), text: "" } : null;
    const attention = channel.attention && typeof channel.attention === "object" ? { ...(channel.attention as Row), reason: null } : null;
    return { ...channel, lastLine, attention };
}

/** A kept answer as it goes to disk: its names and titles, without the words
 *  people and Lemmas said. Of the kept lists two carry such words:
 *
 *  - what waits on the reader across the organization
 *    (`conversation-asks`/`organization`): a need's `detail` is the question
 *    as asked, up to 4,000 characters, or why a contact asked for a person,
 *    and its `choices` are the answers a question offered in its chat;
 *  - a Lemma's shared chats (`channel`/`list`): each chat's `lastLine` says
 *    what was last said in it, and its `attention.reason` why a contact is
 *    waiting.
 *
 *  Those are left out; the rest of each item stays, so the frame draws the
 *  same, and the read that follows on arrival brings the words back. A
 *  chat's title, a need's one-line title and a pause's one-line summary are
 *  kept: they are what the lists show. */
export function withoutWords(queryKey: readonly unknown[], data: unknown): unknown {
    const [head, scope, kind] = queryKey;
    if (head === "conversation-asks" && scope === "organization") {
        return rows(data)?.map((need) => ({ ...need, detail: null, choices: [] })) ?? data;
    }
    if (head === "channel" && kind === "list" && data && typeof data === "object") {
        const infinite = data as { pages?: unknown };
        if (!Array.isArray(infinite.pages)) return data;
        return {
            ...infinite,
            pages: infinite.pages.map((page) => {
                const items = page && typeof page === "object" ? rows((page as Row).items) : null;
                return items ? { ...(page as Row), items: items.map(chatWithoutWords) } : page;
            }),
        };
    }
    return data;
}

/** Whether a kept record may be read back now, for this person. `api` is the
 *  one this browser talks to now, not the one it was built with: `/connect`
 *  and the deployment can point it elsewhere. */
export function readable(record: unknown, now: number, owner: string | null, build = BUILD, api = upstreamUrl()): record is KeptRecord {
    if (!owner || !record || typeof record !== "object") return false;
    const kept = record as Partial<KeptRecord>;
    return kept.owner === owner && kept.build === build && kept.api === api
        && typeof kept.at === "number" && now - kept.at <= KEPT_FOR_MS && now >= kept.at
        && typeof kept.state === "object" && kept.state !== null;
}

/** Who the device last kept a workspace for, as the session records it. */
function owner(): string | null {
    try {
        return localStorage.getItem(key("workspace-owner"));
    } catch {
        return null;
    }
}

function open(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = () => request.result.createObjectStore(STORE);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error("blocked"));
    });
}

/** One request against the store, the database closed after it. */
async function run<T>(mode: IDBTransactionMode, ask: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await open();
    try {
        return await new Promise<T>((resolve, reject) => {
            const transaction = db.transaction(STORE, mode);
            const request = ask(transaction.objectStore(STORE));
            transaction.oncomplete = () => resolve(request.result);
            transaction.onerror = () => reject(transaction.error);
            transaction.onabort = () => reject(transaction.error);
        });
    } finally {
        db.close();
    }
}

function within<T>(promise: Promise<T>, ms: number, otherwise: T): Promise<T> {
    return Promise.race([promise, new Promise<T>((resolve) => setTimeout(() => resolve(otherwise), ms))]);
}

const usable = () => typeof indexedDB !== "undefined";

/** Put back what the device kept for the person signed in here, before the
 *  frame draws. Anything not theirs, too old, or from another build is
 *  thrown away instead. Never fails, and never waits long. */
export async function restoreFromDevice(client: QueryClient): Promise<void> {
    if (!usable()) return;
    try {
        const record = await within(run("readonly", (store) => store.get(RECORD)), READ_WITHIN_MS, undefined);
        if (record === undefined) return;
        /* Asked again now, not when the read began: the session may have
           found someone else signed in while the device was answering. */
        if (!readable(record, Date.now(), owner())) {
            void forgetOnDevice();
            return;
        }
        hydrate(client, record.state);
    } catch {
        /* Private windows and full disks: the app opens the slow way. */
    }
}

/** Keep the frame's lists on the device as they change, for the person
 *  signed in. Returns the way to stop. */
export function keepOnDevice(client: QueryClient): () => void {
    if (!usable()) return () => undefined;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const write = () => {
        timer = null;
        const who = owner();
        if (!who) return;
        const record: KeptRecord = { build: BUILD, api: upstreamUrl(), owner: who, at: Date.now(), state: keptState(client) };
        void run("readwrite", (store) => store.put(record, RECORD)).catch(() => undefined);
    };
    /* At most once a second, from the first change: a cache that never goes
       quiet -- Lemmas polled every minute, one after another -- is still
       written. */
    const soon = () => {
        if (!timer) timer = setTimeout(write, WRITE_AFTER_MS);
    };
    /* Written on the way out too, so the last second is not lost. */
    const now = () => {
        if (!timer) return;
        clearTimeout(timer);
        write();
    };
    const stop = client.getQueryCache().subscribe((event) => {
        if (event.type === "updated" || event.type === "removed") soon();
    });
    window.addEventListener("pagehide", now);
    return () => {
        stop();
        window.removeEventListener("pagehide", now);
        if (timer) clearTimeout(timer);
    };
}

/** Wipe what the device kept: on signing out, and when someone else signs
 *  in. Never fails, and never waits long. */
export async function forgetOnDevice(): Promise<void> {
    if (!usable()) return;
    await within(run("readwrite", (store) => store.delete(RECORD)).catch(() => undefined), READ_WITHIN_MS, undefined);
}
