import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";

/* The caches folder, in memory. */
const files = new Map<string, string>();
vi.mock("expo-file-system/legacy", () => ({
    cacheDirectory: "file:///caches/",
    getInfoAsync: async (path: string) => ({ exists: files.has(path) }),
    readAsStringAsync: async (path: string) => files.get(path) ?? "",
    writeAsStringAsync: async (path: string, body: string) => void files.set(path, body),
    deleteAsync: async (path: string) => void files.delete(path),
}));
vi.mock("expo-constants", () => ({ default: { expoConfig: { version: "0.1.0", ios: { buildNumber: "7" } } } }));
const moved: ((state: string) => void)[] = [];
vi.mock("react-native", () => ({
    AppState: {
        addEventListener: (_: string, listener: (state: string) => void) => {
            moved.push(listener);
            return { remove: () => moved.splice(moved.indexOf(listener), 1) };
        },
    },
}));

import { buildOf, forgetOnDevice, keepOnDevice, keptOnDevice, KEPT_FOR_MS, readable, restoreFromDevice, type KeptRecord } from "../src/session/device-cache";

const FILE = "file:///caches/lemma-device-cache.json";
const SERVER = "hosted";
const ME = { server: SERVER, owner: "user-1" };

const LEMMAS = [SERVER, "lemmas", "org-1"];
const PULSE = [SERVER, "pulse", "org-1", "pod-1"];
const NEEDS = [SERVER, "pulse", "needs", "org-1", "pod-1"];
const PLACES = [SERVER, "work", "pod-1", "surfaces"];
const LINES = [SERVER, "lines", "pod-1", "channel-1"];
const ROWS = [SERVER, "tables", "pod-1"];

/** A cache reading what Home reads, and a chat and a table besides. */
function read(client: QueryClient): void {
    client.setQueryData([SERVER, "orgs"], [{ id: "org-1", name: "Acme" }]);
    client.setQueryData(LEMMAS, [{ id: "pod-1", name: "Support" }]);
    client.setQueryData(PULSE, [{ lemma: { id: "pod-1" }, conversations: [{ id: "c-1", title: "Refunds" }] }]);
    client.setQueryData(NEEDS, [{ key: "n-1", title: "Approve the refund?" }]);
    client.setQueryData(PLACES, [{ name: "WhatsApp" }]);
    client.setQueryData(LINES, [{ text: "the words of a chat" }]);
    client.setQueryData(ROWS, [{ id: 1, amount: 40 }]);
}

/** A launch that read all that while the phone kept it, a second later. */
async function launchedOnce(): Promise<void> {
    const client = new QueryClient();
    const stop = keepOnDevice(client, ME, () => true);
    read(client);
    await vi.advanceTimersByTimeAsync(1000);
    stop();
    expect(files.has(FILE)).toBe(true);
}

/** Keeping started, and the reads already come in. */
function keeping(still: () => boolean = () => true): { client: QueryClient; stop: () => void } {
    const client = new QueryClient();
    const stop = keepOnDevice(client, ME, still);
    read(client);
    return { client, stop };
}

function kept(): KeptRecord {
    return JSON.parse(files.get(FILE) ?? "null") as KeptRecord;
}

beforeEach(() => {
    vi.useFakeTimers();
    files.clear();
    moved.length = 0;
});

afterEach(() => {
    vi.useRealTimers();
});

describe("what the phone keeps", () => {
    it("keeps the lists Home and a Lemma's page are drawn from", () => {
        for (const key of [[SERVER, "me"], [SERVER, "orgs"], LEMMAS, PULSE, NEEDS, [SERVER, "apps", "org-1", "pod-1"], PLACES]) {
            expect(keptOnDevice(key, SERVER), JSON.stringify(key)).toBe(true);
        }
    });

    it("never keeps what is inside a chat, a table or a file", () => {
        for (const key of [LINES, ROWS, [SERVER, "chat-file", "pod-1", "a.pdf"], [SERVER, "card", "n-1", "call-1"], [SERVER, "work", "pod-1", "members"]]) {
            expect(keptOnDevice(key, SERVER), JSON.stringify(key)).toBe(false);
        }
    });

    it("never keeps sample data or another server's answers", () => {
        expect(keptOnDevice(["sample", "lemmas", "org-1"], SERVER)).toBe(false);
        expect(keptOnDevice(["dev-stack", "lemmas", "org-1"], SERVER)).toBe(false);
        /* The choices kept in the keystore are not server answers. */
        expect(keptOnDevice(["org-choice"], SERVER)).toBe(false);
    });

    it("stamps a release with its version and build, and every development build as one", () => {
        expect(buildOf({ version: "0.1.0", ios: { buildNumber: "7" } }, false)).toBe("0.1.0+7");
        expect(buildOf({ version: "0.1.0", android: { versionCode: 3 } }, false)).toBe("0.1.0+3");
        expect(buildOf({ version: "0.1.0", ios: { buildNumber: "7" } }, true)).toBe("dev");
    });
});

describe("whose it is", () => {
    const now = Date.UTC(2026, 10, 24);
    const record: KeptRecord = { ...ME, build: "0.1.0+7", at: now - 60_000, state: { queries: [], mutations: [] } };

    it("is read back for the person who wrote it, on the same server and build", () => {
        expect(readable(record, now, ME, "0.1.0+7")).toBe(true);
    });

    it("is not read for anybody else, anywhere else, or by another build", () => {
        expect(readable(record, now, { ...ME, owner: "user-2" }, "0.1.0+7")).toBe(false);
        expect(readable(record, now, { ...ME, server: "dev-stack" }, "0.1.0+7")).toBe(false);
        expect(readable(record, now, ME, "0.1.1+8")).toBe(false);
    });

    it("is not read after a week, or from a clock that has gone backwards", () => {
        expect(readable({ ...record, at: now - KEPT_FOR_MS - 1 }, now, ME, "0.1.0+7")).toBe(false);
        expect(readable({ ...record, at: now + 60_000 }, now, ME, "0.1.0+7")).toBe(false);
        expect(readable({ ...record, state: null }, now, ME, "0.1.0+7")).toBe(false);
        expect(readable("not a record", now, ME, "0.1.0+7")).toBe(false);
    });
});

describe("between launches", () => {
    it("opens on the lists seen last, and nothing else", async () => {
        await launchedOnce();

        const next = new QueryClient();
        await restoreFromDevice(next, ME);

        expect(next.getQueryData(LEMMAS)).toEqual([{ id: "pod-1", name: "Support" }]);
        /* A need comes back with its title, and without the words behind it. */
        expect(next.getQueryData(NEEDS)).toEqual([{ key: "n-1", title: "Approve the refund?", detail: null, choices: [] }]);
        expect(next.getQueryData(PLACES)).toEqual([{ name: "WhatsApp" }]);
        expect(next.getQueryData(LINES)).toBeUndefined();
        expect(next.getQueryData(ROWS)).toBeUndefined();
        expect(files.get(FILE)).not.toContain("the words of a chat");
    });

    it("reads each kept list again once a screen shows it, however recently it was kept", async () => {
        await launchedOnce();
        const keptAt = Date.now();
        /* The next launch a minute later: well inside the Lemmas' five minutes. */
        await vi.advanceTimersByTimeAsync(60_000);
        const next = new QueryClient();
        await restoreFromDevice(next, ME);

        let reads = 0;
        const observer = new QueryObserver(next, {
            queryKey: LEMMAS,
            queryFn: async () => {
                reads += 1;
                return [{ id: "pod-2", name: "Sales" }];
            },
            staleTime: 5 * 60_000,
        });
        /* Drawn at once from what was kept, and stale, so it is read again. */
        expect(observer.getCurrentResult().data).toEqual([{ id: "pod-1", name: "Support" }]);
        expect(observer.getCurrentResult().isStale).toBe(true);
        expect(next.getQueryState(LEMMAS)?.dataUpdatedAt).toBeLessThanOrEqual(keptAt);

        const stop = observer.subscribe(() => undefined);
        await vi.advanceTimersByTimeAsync(10 * 60_000);
        stop();

        expect(reads).toBe(1);
        expect(next.getQueryData(LEMMAS)).toEqual([{ id: "pod-2", name: "Sales" }]);
    });

    it("writes once for a burst of answers, a second after the first", async () => {
        const { client, stop } = keeping();
        await vi.advanceTimersByTimeAsync(500);
        expect(files.has(FILE)).toBe(false);
        client.setQueryData(LEMMAS, [{ id: "pod-2", name: "Sales" }]);
        await vi.advanceTimersByTimeAsync(500);
        stop();

        const lemmas = kept().state.queries.find((query) => JSON.stringify(query.queryKey) === JSON.stringify(LEMMAS));
        expect(lemmas?.state.data).toEqual([{ id: "pod-2", name: "Sales" }]);
    });

    it("writes at once on the way to the background", async () => {
        const { stop } = keeping();
        for (const listener of [...moved]) listener("background");
        await vi.advanceTimersByTimeAsync(0);
        expect(files.has(FILE)).toBe(true);
        stop();
        expect(moved).toEqual([]);
    });

    it("writes nothing once its person is no longer the one signed in", async () => {
        let signedIn = true;
        const { stop } = keeping(() => signedIn);
        signedIn = false;
        await vi.advanceTimersByTimeAsync(1000);
        stop();
        expect(files.has(FILE)).toBe(false);
    });

    it("writes nothing after it is stopped", async () => {
        const { stop } = keeping();
        stop();
        await vi.advanceTimersByTimeAsync(1000);
        expect(files.has(FILE)).toBe(false);
    });

    it("keeps names and titles, and none of the words said in a chat", async () => {
        const client = new QueryClient();
        const stop = keepOnDevice(client, ME, () => true);
        client.setQueryData(NEEDS, [
            {
                key: "message:m-1",
                kind: "MESSAGE",
                title: "Refund the March invoice?",
                detail: "Refund the March invoice?\nThe customer says the second charge was a mistake, on the card ending 4242.",
                choices: ["Refund it", "Ask them first"],
            },
            { key: "contact:ch-1", kind: "CONTACT", title: "Maria Chen", detail: "My order never arrived and I want my money back", choices: [] },
        ]);
        client.setQueryData(PULSE, [
            {
                lemma: { id: "pod-1", name: "Support" },
                conversations: [{ id: "c-1", title: "Refunds" }],
                channels: [
                    {
                        id: "ch-1",
                        kind: "CONTACT",
                        title: "Maria Chen",
                        topic: null,
                        lastLine: { sender: { kind: "CONTACT", id: "p-1", name: "Maria Chen" }, text: "Where is my parcel? It was due on Monday.", createdAt: "2026-11-24T10:00:00Z" },
                        attention: { since: "2026-11-24T10:00:00Z", reason: "Asked to speak to a person about a refund" },
                    },
                ],
            },
        ]);
        client.setQueryData([SERVER, "apps", "org-1", "pod-1"], [{ id: "a-1", name: "tickets", description: "Every ticket, by status" }]);
        await vi.advanceTimersByTimeAsync(1000);
        stop();

        const onDisk = files.get(FILE) ?? "";
        for (const said of ["second charge", "card ending 4242", "Refund it", "Ask them first", "never arrived", "Where is my parcel", "speak to a person"]) {
            expect(onDisk, said).not.toContain(said);
        }
        for (const named of ["Refund the March invoice?", "Maria Chen", "Refunds", "Support", "Every ticket, by status"]) {
            expect(onDisk, named).toContain(named);
        }

        /* Read back, the lists draw the same, with the words left out. */
        const next = new QueryClient();
        await restoreFromDevice(next, ME);
        const [question] = next.getQueryData<{ title: string; detail: unknown; choices: unknown }[]>(NEEDS) ?? [];
        expect(question).toMatchObject({ title: "Refund the March invoice?", detail: null, choices: [] });
        const [{ channels: [chat] }] = next.getQueryData<{ channels: { lastLine: { text: string; createdAt: string }; attention: { reason: unknown; since: string } }[] }[]>(PULSE) ?? [];
        expect(chat.lastLine).toMatchObject({ text: "", createdAt: "2026-11-24T10:00:00Z" });
        expect(chat.attention).toEqual({ since: "2026-11-24T10:00:00Z", reason: null });
    });

    it("never keeps a message on its way to the server", async () => {
        const { client, stop } = keeping();
        void client.getMutationCache().build(client, { mutationFn: async (text: string) => text }).execute("unsent words").catch(() => undefined);
        await vi.advanceTimersByTimeAsync(1000);
        stop();
        expect(kept().state.mutations).toEqual([]);
        expect(files.get(FILE)).not.toContain("unsent words");
    });

    it("keeps a list whose last read failed, as the answer it holds", async () => {
        /* A launch without signal: every read fails behind what was kept. */
        const { client, stop } = keeping();
        await client.fetchQuery({ queryKey: LEMMAS, queryFn: () => Promise.reject(new Error("offline")), retry: false, staleTime: 0 }).catch(() => undefined);
        expect(client.getQueryState(LEMMAS)?.status).toBe("error");
        await vi.advanceTimersByTimeAsync(1000);
        stop();

        const next = new QueryClient();
        await restoreFromDevice(next, ME);
        expect(next.getQueryData(LEMMAS)).toEqual([{ id: "pod-1", name: "Support" }]);
        expect(next.getQueryState(LEMMAS)?.status).toBe("success");
        expect(next.getQueryState(LEMMAS)?.error).toBeNull();
    });

    it("gives somebody else nothing, and wipes what it held", async () => {
        await launchedOnce();

        const next = new QueryClient();
        await restoreFromDevice(next, { ...ME, owner: "user-2" });
        await vi.advanceTimersByTimeAsync(0);

        expect(next.getQueryData(LEMMAS)).toBeUndefined();
        expect(files.has(FILE)).toBe(false);
    });

    it("has nothing to give after signing out", async () => {
        await launchedOnce();
        await forgetOnDevice();

        const next = new QueryClient();
        await restoreFromDevice(next, ME);
        expect(next.getQueryData(LEMMAS)).toBeUndefined();
    });

    it("opens the slow way when the file is not a record", async () => {
        files.set(FILE, "{ not json");
        const next = new QueryClient();
        await expect(restoreFromDevice(next, ME)).resolves.toBeUndefined();
        expect(next.getQueryData(LEMMAS)).toBeUndefined();
    });
});
