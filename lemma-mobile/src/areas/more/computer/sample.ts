import * as FileSystem from "expo-file-system/legacy";
import type { ComputerSource, SignInOutcome, WebLogin } from "./source";
import { childrenOf, dayOf, SAMPLE_HOME, SAMPLE_SIGN_IN, SAMPLE_WORKSPACE, sampleChatFolders, sampleLogins, sampleTree, utf8Length, type SampleNode } from "./sample-tree";
import type { Listing } from "@web/computer/machine";
import type { WorkspaceStatus } from "@web/computer/startup";

/** The sample computer: ready, with a small tree under `/home/user`, a
 *  browser signed in to a few sites, and the press-list chat's sign-in
 *  waiting. Writes stay in memory while the app is open.
 *
 *  It behaves like a real one in the ways a person can see: it falls asleep
 *  after a quarter of an hour without use, and waking it goes through the
 *  download and the start. What it cannot do is show a screen or a picture:
 *  there is no machine behind it, and the screen says so. */

/** A real computer is released after fifteen idle minutes. */
const IDLE_MS = 15 * 60_000;
/** How long the sample takes to wake: a download, then a start. */
const DOWNLOAD_MS = 7_000;
const START_MS = 4_000;
const IMAGE_MB = 1_240;
/** Entries per page, small so "Load more" can be tried (node_modules). */
const PAGE = 12;

const NO_PICTURE = "In sample data there is no computer to read this picture from.";
const NO_FILE = "In sample data there is no computer to read this file from.";

export function computerSample(): ComputerSource {
    const now = () => Date.now();
    const tree = sampleTree(now());
    const folders = sampleChatFolders(now());
    let logins: WebLogin[] = sampleLogins(now());
    let lastUse = now();
    let wokeAt: number | null = null;
    const answered = new Map<string, SignInOutcome>();

    const asleep = () => wokeAt === null && now() - lastUse > IDLE_MS;
    const use = () => {
        lastUse = now();
    };
    const wake = () => {
        if (asleep()) wokeAt = now();
        use();
    };
    /* Woken, and still coming up. */
    const phase = (): WorkspaceStatus["state"] => {
        if (asleep()) return "asleep";
        if (wokeAt === null) return "ready";
        const since = now() - wokeAt;
        if (since < DOWNLOAD_MS) return "downloading";
        if (since < DOWNLOAD_MS + START_MS) return "starting";
        wokeAt = null;
        use();
        return "ready";
    };
    const node = (path: string): SampleNode => {
        const found = tree.get(path);
        if (!found || found.kind === "directory") throw Object.assign(new Error("There is no file at " + path + "."), { statusCode: 404 });
        return found;
    };
    const listing = (path: string, extra: Partial<Listing>): Listing => ({
        path,
        home_root: SAMPLE_HOME,
        workspace_root: SAMPLE_WORKSPACE,
        sleeping: false,
        truncated: false,
        next_after: null,
        exists: true,
        entries: [],
        ...extra,
    });

    return {
        machine: false,

        async status() {
            const state = phase();
            if (state === "downloading") {
                const done = Math.round((IMAGE_MB * (now() - (wokeAt ?? now()))) / DOWNLOAD_MS);
                return { state, detail: "The first start after an update fetches its software.", done_mb: Math.min(done, IMAGE_MB), total_mb: IMAGE_MB };
            }
            if (state === "starting") return { state, detail: "Starting its services." };
            return { state, detail: null };
        },

        async files(path, wakeIt, after) {
            const at = (path ?? SAMPLE_WORKSPACE).replace(/\/+$/, "") || SAMPLE_HOME;
            if (wakeIt) wake();
            if (asleep()) return listing(at, { sleeping: true });
            use();
            const here = tree.get(at);
            if (!here || here.kind !== "directory") return listing(at, { exists: false });
            const all = childrenOf(tree, at).map(({ text: _text, ...entry }) => entry);
            const from = after ? all.findIndex((entry) => entry.path > after) : 0;
            const start = from < 0 ? all.length : from;
            const page = all.slice(start, start + PAGE);
            const more = start + PAGE < all.length;
            return listing(at, { entries: page, truncated: more, next_after: more ? page[page.length - 1]?.path ?? null : null });
        },

        async browser() {
            return asleep() ? "asleep" : "running";
        },

        async chatFolder(_podId, conversationId) {
            return folders[conversationId] ?? `${SAMPLE_WORKSPACE}/c/${dayOf(now())}/${conversationId}`;
        },

        async stat(path) {
            return { size_bytes: node(path).size_bytes };
        },

        async text(path, limit) {
            const found = node(path);
            use();
            if (found.text === undefined) return { text: "", bytes: Math.min(found.size_bytes, limit) };
            return { text: found.text, bytes: Math.min(utf8Length(found.text), limit) };
        },

        async download(path, limit) {
            const found = node(path);
            use();
            if (found.text === undefined) throw new Error(/\.(png|jpe?g|gif|webp|svg)$/i.test(found.name) ? NO_PICTURE : NO_FILE);
            const uri = `${FileSystem.cacheDirectory}computer-sample-${Date.now()}-${found.name}`;
            await FileSystem.writeAsStringAsync(uri, found.text);
            return { uri, bytes: Math.min(utf8Length(found.text), limit) };
        },

        async browserAccess() {
            throw new Error("In sample data there is no browser to open.");
        },

        async logins(wakeIt) {
            if (wakeIt) wake();
            if (asleep()) return { items: [], sleeping: true };
            return { items: logins.map((login) => ({ ...login })), sleeping: false };
        },

        async signOutOf(origin) {
            if (asleep()) throw new Error("Signing out needs the computer running: it really signs the browser out. Wake it first.");
            use();
            const site = origin.replace(/^https?:\/\//, "");
            logins = logins.filter((login) => login.site !== site);
            return { site, forgotten: true };
        },

        async signIn(conversationId, toolCallId) {
            const waiting = SAMPLE_SIGN_IN;
            if (conversationId !== waiting.conversationId || toolCallId !== waiting.request.tool_call_id || answered.has(toolCallId)) {
                throw Object.assign(new Error("No sign-in is waiting there."), { statusCode: 404 });
            }
            return { ...waiting.request };
        },

        async answerSignIn(conversationId, toolCallId, signedIn) {
            const waiting = SAMPLE_SIGN_IN;
            if (conversationId !== waiting.conversationId || toolCallId !== waiting.request.tool_call_id) {
                throw Object.assign(new Error("No sign-in is waiting there."), { statusCode: 404 });
            }
            use();
            const outcome: SignInOutcome = { origin: waiting.request.origin, signed_in: signedIn, working: signedIn };
            answered.set(toolCallId, outcome);
            if (signedIn) {
                const site = new URL(waiting.request.origin).hostname.split(".").slice(-2).join(".");
                logins = [{ site, cookie_count: 3, expires: new Date(now() + 30 * 86_400_000).toISOString(), signed_in: true }, ...logins.filter((login) => login.site !== site)];
            }
            return outcome;
        },

        async currentPage(origin) {
            return origin.replace(/\/+$/, "") + "/login";
        },

        async fitDisplay() {
            return { size: null };
        },
    };
}
