import Constants from "expo-constants";
import * as FileSystem from "expo-file-system/legacy";
import { AppState } from "react-native";
import { dehydrate, hydrate, type DehydratedState, type QueryClient } from "@tanstack/react-query";

/* What the app keeps on this phone between launches, so it opens on what the
 * person saw last rather than on "Reading your Lemmas", and the network
 * catches up behind it: the lists Home and a Lemma's page are drawn from --
 * the organizations, the Lemmas, what moved in each and their chats, what
 * waits on the person, the apps, a Lemma's places. Names and titles only:
 * a chat's title but not its last line, a need's one-line title (a
 * question's first line, what an approval is for, who is waiting) but not
 * the question in full, what a contact wrote or the answers offered
 * (`withoutWords`). Never what is inside a table or a file, and never a
 * change still on its way to the API.
 *
 * The web's `session/device-cache.ts`, on a phone: one record, in a file in
 * the app's caches folder (not backed up, and the system may clear it, which
 * costs only a slower launch), written at most once a second while the cache
 * changes and again as the app goes to the background. It is the signed-in
 * person's: read back only for them, on the same server and the same build,
 * within a week, and wiped on signing out or when somebody signs in. */

const FILE = `${FileSystem.cacheDirectory ?? ""}lemma-device-cache.json`;
/** Older than this, it says more about the past than the present. */
export const KEPT_FOR_MS = 7 * 86_400_000;
/** One write for a burst of answers, not one per answer. */
const WRITE_AFTER_MS = 1000;
/** A file too slow to read is one the launch does not wait on. */
const READ_WITHIN_MS = 500;

/** Whom a record is kept for: a person, on a server. */
export interface Holder {
    server: string;
    owner: string;
}

export interface KeptRecord extends Holder {
    build: string;
    at: number;
    state: DehydratedState;
}

interface BuiltConfig {
    version?: string;
    ios?: { buildNumber?: string };
    android?: { versionCode?: number };
}

/** The build a record was written by, so what an older build kept -- whose
 *  answers may be shaped differently -- is dropped rather than read as this
 *  one's. Every development build is one build, as on the web. */
export function buildOf(config: BuiltConfig | null | undefined, development: boolean): string {
    if (development) return "dev";
    const number = config?.ios?.buildNumber ?? config?.android?.versionCode ?? "";
    return `${config?.version ?? "0"}+${number}`;
}

const BUILD = buildOf(Constants.expoConfig as BuiltConfig | null, typeof __DEV__ !== "undefined" && __DEV__);

/** The second words of the keys worth keeping (the first is the server):
 *  lists, each small, each read before Home can be drawn. `pulse` covers
 *  what moved in each Lemma and, under `pulse/needs`, what waits on the
 *  person. */
const LISTS: ReadonlySet<string> = new Set(["me", "orgs", "lemmas", "pulse", "apps"]);

/** Whether a query is one the phone keeps for `server`. */
export function keptOnDevice(queryKey: readonly unknown[], server: string): boolean {
    const [at, head, , kind] = queryKey;
    if (at !== server || typeof head !== "string") return false;
    if (LISTS.has(head)) return true;
    /* A Lemma's places, which its page's scene is drawn with. */
    return head === "work" && kind === "surfaces";
}

/** What of the cache the phone keeps: the lists that have answered, and no
 *  mutations -- a message being sent would be kept words and all.
 *
 *  A list whose last read failed is kept too, as the answer it holds: a
 *  query that fails again after answering is an "error" holding its old
 *  data, and keeping only the successes would let one launch without signal
 *  cost the next launch every list.
 *
 *  Every kept list comes back invalidated, so it is read again as soon as a
 *  screen shows it, whatever its `staleTime`: a launch a minute after the
 *  last write would otherwise take the kept answer as fresh and not read
 *  the Lemmas again for up to five minutes. Its `dataUpdatedAt` stays the
 *  time it was read, which is still true. */
export function keptState(client: QueryClient, server: string): DehydratedState {
    const state = dehydrate(client, {
        shouldDehydrateQuery: (query) => query.state.data !== undefined && keptOnDevice(query.queryKey, server),
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

/** A kept answer as it goes to disk: its names and titles, without the words
 *  people and Lemmas said. Of the kept lists only two carry such words:
 *
 *  - what waits on the person (`pulse/needs`): a need's `detail` is the
 *    question as asked, up to 4,000 characters, or what a contact wrote, and
 *    its `choices` are the answers a question offered in its chat;
 *  - what moved in each Lemma (`pulse`): a chat's `lastLine` says what was
 *    last said in it, and its `attention.reason` why a contact is waiting.
 *
 *  Those are left out; the rest of each item stays, so the lists draw the
 *  same, and the read that follows on arrival brings the words back. The
 *  organizations, the Lemmas, the apps, you and a Lemma's places carry names
 *  only. */
export function withoutWords(queryKey: readonly unknown[], data: unknown): unknown {
    if (queryKey[1] !== "pulse") return data;
    const list = rows(data);
    if (!list) return data;
    if (queryKey[2] === "needs") return list.map((need) => ({ ...need, detail: null, choices: [] }));
    return list.map((entry) => {
        const channels = rows(entry.channels);
        if (!channels) return entry;
        return {
            ...entry,
            channels: channels.map((channel) => {
                const lastLine = channel.lastLine && typeof channel.lastLine === "object" ? { ...(channel.lastLine as Row), text: "" } : null;
                const attention = channel.attention && typeof channel.attention === "object" ? { ...(channel.attention as Row), reason: null } : null;
                return { ...channel, lastLine, attention };
            }),
        };
    });
}

/** Whether a kept record may be read back now, for this person here. */
export function readable(record: unknown, now: number, holder: Holder, build = BUILD): record is KeptRecord {
    if (!record || typeof record !== "object") return false;
    const kept = record as Partial<KeptRecord>;
    return kept.owner === holder.owner && kept.server === holder.server && kept.build === build
        && typeof kept.at === "number" && now - kept.at <= KEPT_FOR_MS && now >= kept.at
        && typeof kept.state === "object" && kept.state !== null;
}

/* One file operation at a time, in the order asked: a wipe asked after a
   write lands after it, so the write cannot bring back what was wiped. */
let queue: Promise<unknown> = Promise.resolve();
function inTurn<T>(work: () => Promise<T>): Promise<T> {
    const next = queue.then(work, work);
    queue = next.catch(() => undefined);
    return next;
}

function within<T>(promise: Promise<T>, ms: number, otherwise: T): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const late = new Promise<T>((resolve) => {
        timer = setTimeout(() => resolve(otherwise), ms);
    });
    return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

async function readRecord(): Promise<unknown> {
    const info = await FileSystem.getInfoAsync(FILE);
    if (!info.exists) return undefined;
    return JSON.parse(await FileSystem.readAsStringAsync(FILE)) as unknown;
}

/** Put back what the phone kept for this person here, before the first
 *  screen draws. Anything not theirs, too old, or from another build is
 *  thrown away instead. Never fails, and never waits long. */
export async function restoreFromDevice(client: QueryClient, holder: Holder): Promise<void> {
    try {
        const record = await within(inTurn(readRecord), READ_WITHIN_MS, undefined);
        if (record === undefined) return;
        if (!readable(record, Date.now(), holder)) {
            void forgetOnDevice();
            return;
        }
        hydrate(client, record.state);
    } catch {
        /* Unreadable: the app opens the slow way, and the next write
           replaces it. */
    }
}

/** Keep the lists on the phone as they change, for `holder`, while `still`
 *  says they are the one signed in. Returns the way to stop. */
export function keepOnDevice(client: QueryClient, holder: Holder, still: () => boolean): () => void {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const write = () => {
        timer = null;
        if (!still()) return;
        const record: KeptRecord = { ...holder, build: BUILD, at: Date.now(), state: keptState(client, holder.server) };
        const body = JSON.stringify(record);
        void inTurn(() => FileSystem.writeAsStringAsync(FILE, body)).catch(() => undefined);
    };
    /* At most once a second, from the first change: a cache that never goes
       quiet -- what moved, read again every minute -- is still written. */
    const soon = () => {
        if (!timer) timer = setTimeout(write, WRITE_AFTER_MS);
    };
    /* Written on the way to the background too, so the last second is not
       lost when the system ends the app there. */
    const now = () => {
        if (!timer) return;
        clearTimeout(timer);
        write();
    };
    const stop = client.getQueryCache().subscribe((event) => {
        if (event.type === "updated" || event.type === "removed") soon();
    });
    const leaving = AppState.addEventListener("change", (state) => {
        if (state !== "active") now();
    });
    return () => {
        stop();
        leaving.remove();
        if (timer) clearTimeout(timer);
        timer = null;
    };
}

/** Wipe what the phone kept: on signing out, and when somebody signs in.
 *  Never fails. */
export async function forgetOnDevice(): Promise<void> {
    await inTurn(() => FileSystem.deleteAsync(FILE, { idempotent: true })).catch(() => undefined);
}
