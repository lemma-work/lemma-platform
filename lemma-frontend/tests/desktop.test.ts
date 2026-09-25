import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import { appFrameMode, desktopBridgeAvailable, desktopInfo, invoke, isDesktop } from "../src/desktop/bridge.ts";
import { resolveAppFrame } from "../src/desktop/pod-apps.ts";
import { copyText } from "../src/desktop/clipboard.ts";
import { openExternal, openExternalWhenReady } from "../src/desktop/open-external.ts";
import type { AgentHostStatus, AgentHostTarget } from "../src/desktop/agent-host.ts";
import { readStatus } from "../src/desktop/agent-host.ts";
import {
    claimAttempt,
    connectFailure,
    connectThisComputer,
    resetAutoConnectForTests,
    retryAutoConnect,
    tellSession,
    wasRemoved,
    type ConnectDeps,
} from "../src/desktop/auto-connect.ts";
import { capitalised, describeThisComputer, selectWorkspaceTarget, thisComputer } from "../src/desktop/this-computer.ts";
import { adoptConversationFolder, bindFolder, folderLabel, readFolder, unbindFolder } from "../src/desktop/folders.ts";
import { sandboxImageNotice, shouldKeepPolling } from "../src/desktop/sandbox-images.ts";
import { requestedSection } from "../src/desktop/open-settings.ts";
import { browserSignInUrl, requestIdFromSearch, shouldUseBrowserHandoff } from "../src/desktop/auth-handoff.ts";

/* ── a pretend page ────────────────────────────────────────────────── */

type Call = { command: string; args?: Record<string, unknown> };

interface Page {
    calls: Call[];
    opened: { url: string; features?: string }[];
    assigned: string[];
}

/** Install a `window` shaped like the one this code runs in: a browser when
 *  `shell` is omitted, the desktop app when it is given. */
function page({
    shell,
    info,
    deployment = "hosted",
    hostname = "app.lemma.localhost",
}: {
    shell?: (command: string, args?: Record<string, unknown>) => unknown;
    info?: Record<string, unknown>;
    deployment?: string;
    hostname?: string;
} = {}): Page {
    const state: Page = { calls: [], opened: [], assigned: [] };
    const win: Record<string, unknown> = {
        __LEMMA_SITE__: { analyticsKey: "", analyticsHost: "", deployment },
        location: { hostname, assign: (url: string) => state.assigned.push(url) },
        open: (url: string, _target?: string, features?: string) => {
            state.opened.push({ url, features });
            return null;
        },
    };
    if (shell) {
        win.__TAURI__ = {
            core: {
                invoke: async (command: string, args?: Record<string, unknown>) => {
                    state.calls.push({ command, args });
                    return shell(command, args);
                },
            },
        };
    }
    if (info) win.__LEMMA_DESKTOP__ = info;
    (globalThis as { window?: unknown }).window = win;
    return state;
}

function setNavigator(value: unknown) {
    Object.defineProperty(globalThis, "navigator", { value, configurable: true, writable: true });
}

const originalNavigator = globalThis.navigator;

afterEach(() => {
    delete (globalThis as { window?: unknown }).window;
    delete (globalThis as { document?: unknown }).document;
    setNavigator(originalNavigator);
    resetAutoConnectForTests();
});

/* ── bridge detection ──────────────────────────────────────────────── */

test("a browser is not the desktop app, and cannot invoke anything", async () => {
    page();
    assert.equal(isDesktop(), false);
    assert.equal(desktopInfo(), null);
    assert.equal(desktopBridgeAvailable(), false);
    await assert.rejects(invoke("agent_host_status"), /desktop app/);
});

test("the app on a hosted workspace is the desktop app, without the local bridge", () => {
    page({ shell: () => null, info: { version: "0.8.0", mode: "hosted", platform: "macos" } });
    assert.equal(isDesktop(), true);
    assert.equal(desktopInfo()?.mode, "hosted");
    /* Local commands need a local stack; a cloud workspace in the app has none. */
    assert.equal(desktopBridgeAvailable(), false);
});

test("the local bridge needs both a local deployment and the shell", () => {
    page({ shell: () => null, deployment: "local" });
    assert.equal(desktopBridgeAvailable(), true);
    /* A LAN or public-link browser on the same local deployment has no shell. */
    page({ deployment: "local" });
    assert.equal(desktopBridgeAvailable(), false);
});

test("the app's own window on a shared address has no shell to call", async () => {
    /* Sharing moves the window to the LAN or tunnel origin, which the shell's
       capability does not grant; calling from there only ever failed. */
    for (const hostname of ["192.168.1.20", "example.ngrok.app", "lemma.example.com"]) {
        const state = page({ shell: () => null, deployment: "local", hostname, info: { mode: "local" } });
        assert.equal(isDesktop(), false, hostname);
        assert.equal(desktopBridgeAvailable(), false, hostname);
        await assert.rejects(invoke("agent_host_status"), /desktop app/);
        assert.equal(state.calls.length, 0);
    }
    for (const hostname of ["app.lemma.localhost", "localhost"]) {
        page({ shell: () => null, deployment: "local", hostname });
        assert.equal(desktopBridgeAvailable(), true, hostname);
    }
});

test("invoke passes the command and its arguments through", async () => {
    const state = page({ shell: () => ({ ok: true }) });
    assert.deepEqual(await invoke("agent_host_pair", { url: "u", pairingCode: "c", name: "n" }), { ok: true });
    assert.deepEqual(state.calls, [{ command: "agent_host_pair", args: { url: "u", pairingCode: "c", name: "n" } }]);
});

test("only the macOS app on a local install frames apps through an alias", () => {
    page();
    assert.equal(appFrameMode(), "direct", "a browser frames the app's own URL");
    page({ shell: () => null, deployment: "local", info: { mode: "local", platform: "windows" } });
    assert.equal(appFrameMode(), "direct", "WebView2 treats *.lemma.localhost as one site");
    page({ shell: () => null, deployment: "local", info: { mode: "local", platform: "macos" } });
    assert.equal(appFrameMode(), "alias");
    page({ shell: () => null, deployment: "local", info: { mode: "local" } });
    assert.equal(appFrameMode(), "window", "a shell too old to say cannot alias either");
    page({ deployment: "local", info: { mode: "local", platform: "macos" } });
    assert.equal(appFrameMode(), "window", "no shell to ask: a window, not a signed-out frame");
    page({ shell: () => null, info: { mode: "hosted", platform: "macos" }, hostname: "lemma.work" });
    assert.equal(appFrameMode(), "direct", "a hosted workspace and its apps are one site");
});

test("the frame is the alias the shell hands back, or a window when it will not", async () => {
    const app = "http://orders.apps.lemma.localhost:52414/reports";
    const asked: string[] = [];
    const shell = (answer: unknown) => async (url: string) => { asked.push(url); return answer; };

    assert.deepEqual(await resolveAppFrame(app, "direct", shell(null)), { kind: "frame", src: app });
    assert.deepEqual(await resolveAppFrame(app, "window", shell(null)), { kind: "window" });
    assert.deepEqual(asked, [], "only the alias mode asks the shell");

    const alias = "http://app.lemma.localhost:61001/reports";
    assert.deepEqual(
        await resolveAppFrame(app, "alias", shell({ url: alias, aliased: true })),
        { kind: "frame", src: alias },
    );
    assert.deepEqual(asked, [app], "the shell is asked about the app's own URL");

    /* An older shell refuses the command; a broken answer is not a URL. */
    const refusing = async () => { throw new Error("Command app_frame_url not allowed by ACL"); };
    assert.deepEqual(await resolveAppFrame(app, "alias", refusing), { kind: "window" });
    assert.deepEqual(await resolveAppFrame(app, "alias", shell({ url: "javascript:alert(1)" })), { kind: "window" });
    assert.deepEqual(await resolveAppFrame(app, "alias", shell(null)), { kind: "window" });
});

test("asking for a frame goes through the shell's app_frame_url", async () => {
    const state = page({
        shell: () => ({ url: "http://app.lemma.localhost:61001/", aliased: true }),
        deployment: "local",
        info: { mode: "local", platform: "macos" },
    });
    const frame = await resolveAppFrame(
        "http://orders.apps.lemma.localhost:52414/",
        appFrameMode(),
        (url) => invoke("app_frame_url", { url }),
    );
    assert.deepEqual(frame, { kind: "frame", src: "http://app.lemma.localhost:61001/" });
    assert.deepEqual(state.calls, [{ command: "app_frame_url", args: { url: "http://orders.apps.lemma.localhost:52414/" } }]);
});

/* ── clipboard ─────────────────────────────────────────────────────── */

function fakeDocument(copies: boolean) {
    const copied: string[] = [];
    const doc = {
        body: { appendChild: () => undefined },
        createElement: () => {
            const area = {
                value: "",
                style: {} as Record<string, string>,
                setAttribute: () => undefined,
                select: () => undefined,
                setSelectionRange: () => undefined,
                removed: false,
                remove() { this.removed = true; },
            };
            (doc as { last?: typeof area }).last = area;
            return area;
        },
        execCommand: (command: string) => {
            const area = (doc as { last?: { value: string } }).last;
            if (command === "copy" && copies && area) copied.push(area.value);
            return copies;
        },
    };
    (globalThis as { document?: unknown }).document = doc;
    return copied;
}

test("the async clipboard is used where it exists", async () => {
    const written: string[] = [];
    setNavigator({ clipboard: { writeText: async (text: string) => { written.push(text); } } });
    const copied = fakeDocument(true);
    await copyText("hello");
    assert.deepEqual(written, ["hello"]);
    assert.deepEqual(copied, []);
});

test("without a secure context the older path copies instead", async () => {
    /* WKWebView on http://app.lemma.localhost: no `navigator.clipboard` at all. */
    setNavigator({});
    const copied = fakeDocument(true);
    await copyText("from the desktop app");
    assert.deepEqual(copied, ["from the desktop app"]);
});

test("a denied async clipboard still falls back", async () => {
    setNavigator({ clipboard: { writeText: async () => { throw new Error("denied"); } } });
    const copied = fakeDocument(true);
    await copyText("again");
    assert.deepEqual(copied, ["again"]);
});

test("when neither path works, the caller hears about it", async () => {
    setNavigator({});
    fakeDocument(false);
    await assert.rejects(copyText("nowhere"), /clipboard/);
});

/* ── open-external ─────────────────────────────────────────────────── */

test("an external link opens severed from the workspace", () => {
    const state = page();
    openExternal("https://provider.example/authorize");
    assert.deepEqual(state.opened, [{ url: "https://provider.example/authorize", features: "noopener,noreferrer" }]);
});

test("a mail link goes to the mail handler, not a blank tab", () => {
    const state = page();
    openExternal("mailto:someone@example.test");
    assert.deepEqual(state.opened, []);
    assert.deepEqual(state.assigned, ["mailto:someone@example.test"]);
});

test("a later URL claims its tab in the click in a browser", async () => {
    const state = page();
    const tab = { closed: false, opener: {} as unknown, location: { replaced: "", replace(url: string) { this.replaced = url; } } };
    (globalThis as { window: { open: unknown } }).window.open = (url: string, _target?: string, features?: string) => {
        state.opened.push({ url, features });
        return tab;
    };
    await openExternalWhenReady(Promise.resolve("https://grant.example/x"));
    assert.deepEqual(state.opened, [{ url: "", features: undefined }]);
    assert.equal(tab.opener, null, "the reference is cut by hand");
    assert.equal(tab.location.replaced, "https://grant.example/x");
});

test("in the desktop app a later URL is simply handed to the shell", async () => {
    /* The shell refuses a blank window, so pre-opening one would lose the URL. */
    const state = page({ shell: () => null, info: { mode: "hosted", platform: "macos" } });
    await openExternalWhenReady(Promise.resolve("https://grant.example/x"));
    assert.deepEqual(state.opened, [{ url: "https://grant.example/x", features: "noopener,noreferrer" }]);
});

/* ── this computer's status ────────────────────────────────────────── */

const WORKSPACE = "https://api.lemma.work";

function target(overrides: Partial<AgentHostTarget> = {}): AgentHostTarget {
    return {
        target_id: "t1", host_id: "h1", name: "My Mac", url: WORKSPACE, enabled: true,
        connection_state: "ONLINE", last_connected_at: null, last_error: null,
        active_runs: 0, pending_events: 0, ...overrides,
    };
}

function status(overrides: Partial<AgentHostStatus> = {}): AgentHostStatus {
    return {
        available: true, running: true, desired_running: true, paired: true,
        targets: [target()], uptime_seconds: 10, last_error: null, log: null, ...overrides,
    };
}

test("the shell's loose JSON is narrowed, or refused", () => {
    assert.equal(readStatus(null), null);
    assert.equal(readStatus({ running: true }), null, "no `available` is not a status");
    const read = readStatus({ available: true, running: "yes", targets: "nope" });
    assert.equal(read?.running, false);
    assert.deepEqual(read?.targets, []);
});

test("only the pairing for the workspace on screen counts", () => {
    const local = target({ url: "http://api.lemma.localhost:8000", host_id: "local" });
    const hosted = target({ url: WORKSPACE + "/some/path", host_id: "cloud" });
    assert.equal(selectWorkspaceTarget([local, hosted], WORKSPACE)?.host_id, "cloud");
    assert.equal(selectWorkspaceTarget([local], WORKSPACE), null);
    assert.equal(selectWorkspaceTarget([target({ url: null })], WORKSPACE), null, "no URL matches nothing");
});

test("the planes rank into one reported state", () => {
    const say = (s: AgentHostStatus | null, error: string | null = null, connect: string | null = null) =>
        describeThisComputer(s, error, WORKSPACE, connect, "this Mac").label;

    assert.equal(say(null), "Checking");
    assert.equal(say(null, "ACL refused"), "Unavailable");
    assert.equal(say(status({ available: false })), "Not available");
    assert.equal(say(status({ targets: [] })), "Connecting");
    /* Connecting outranks starting: not paired here is the more specific answer. */
    assert.equal(say(status({ targets: [], running: false })), "Connecting");
    assert.equal(say(status({ running: false })), "Starting");
    assert.equal(say(status()), "Connected");
    assert.equal(say(status({ targets: [target({ connection_state: "OFFLINE", last_error: "401" })] })), "Unreachable");
    assert.equal(say(status({ targets: [target({ connection_state: "OFFLINE" })] })), "Reconnecting");
});

test("a failed connection displaces connecting, and only it offers a retry", () => {
    const failed = describeThisComputer(status({ targets: [] }), null, WORKSPACE, "pairing refused", "this Mac");
    assert.equal(failed.label, "Couldn\u2019t connect");
    assert.equal(failed.detail, "pairing refused");
    assert.equal(failed.retry, true);
    /* Once paired here, an old connect failure is history, not the state. */
    assert.equal(describeThisComputer(status(), null, WORKSPACE, "pairing refused").label, "Connected");
    assert.equal(describeThisComputer(status({ targets: [] }), null, WORKSPACE, null).retry, false);
});

test("the computer is named for what it is", () => {
    page({ shell: () => null, info: { mode: "local", platform: "macos" } });
    assert.equal(thisComputer(), "this Mac");
    page({ shell: () => null, info: { mode: "local", platform: "windows" } });
    assert.equal(thisComputer(), "this PC");
    assert.equal(capitalised("this PC"), "This PC");
});

/* ── auto-connect ──────────────────────────────────────────────────── */

function deps(log: string[], fail?: string): ConnectDeps {
    return {
        createPairing: async (name) => {
            log.push("mint:" + name);
            if (fail) throw new Error(fail);
            return { pairing_code: "code-1" };
        },
        host: {
            start: async () => { log.push("start"); },
            pair: async (url, code, _name, reenable) => {
                log.push(`pair:${url}:${code}` + (reenable ? ":reenable" : ""));
            },
            refresh: async () => { log.push("refresh"); },
        },
    };
}

test("an unpaired computer is paired once per workspace, however many ask", async () => {
    page({ shell: () => null, info: { mode: "hosted", platform: "macos" } });
    const log: string[] = [];
    const unpaired = status({ targets: [] });
    const [first, second] = await Promise.all([
        connectThisComputer(unpaired, WORKSPACE, deps(log)),
        connectThisComputer(unpaired, WORKSPACE, deps(log)),
    ]);
    assert.deepEqual([first, second].sort(), ["connected", "skipped"]);
    assert.deepEqual(log, ["mint:My Mac", `pair:${WORKSPACE}:code-1`, "refresh"]);
});

test("the guard is per workspace origin", () => {
    assert.equal(claimAttempt(WORKSPACE), true);
    assert.equal(claimAttempt(WORKSPACE), false);
    assert.equal(claimAttempt("http://api.lemma.localhost:8000"), true);
});

test("a stopped host is started but not re-paired", async () => {
    const log: string[] = [];
    assert.equal(await connectThisComputer(status({ running: false }), WORKSPACE, deps(log)), "connected");
    assert.deepEqual(log, ["start", "refresh"]);
});

test("a running, paired computer needs nothing", async () => {
    const log: string[] = [];
    assert.equal(await connectThisComputer(status(), WORKSPACE, deps(log)), "skipped");
    assert.deepEqual(log, []);
});

test("a failure is recorded, not retried, until someone asks", async () => {
    const log: string[] = [];
    const unpaired = status({ targets: [] });
    assert.equal(await connectThisComputer(unpaired, WORKSPACE, deps(log, "pairing refused")), "failed");
    assert.equal(connectFailure(), "pairing refused");
    /* The next poll must not mint another code. */
    assert.equal(await connectThisComputer(unpaired, WORKSPACE, deps(log)), "skipped");
    assert.equal(log.filter((entry) => entry.startsWith("mint")).length, 1);

    retryAutoConnect();
    assert.equal(connectFailure(), null);
    assert.equal(await connectThisComputer(unpaired, WORKSPACE, deps(log)), "connected");
    assert.equal(log.filter((entry) => entry.startsWith("mint")).length, 2);
});

test("a pairing that is off, or somebody else's, is not this workspace's", () => {
    const mine = target({ host_id: "mine", user_id: "me" });
    const theirs = target({ host_id: "theirs", user_id: "them" });
    assert.equal(selectWorkspaceTarget([theirs, mine], WORKSPACE, "me")?.host_id, "mine");
    assert.equal(selectWorkspaceTarget([theirs], WORKSPACE, "me"), null, "another person's pairing");
    assert.equal(selectWorkspaceTarget([target({ enabled: false })], WORKSPACE), null, "a pairing the host turned off");
    /* An older shell says whose it is nowhere: taken as the signed-in person's. */
    assert.equal(selectWorkspaceTarget([target({ host_id: "old" })], WORKSPACE, "me")?.host_id, "old");
    assert.equal(
        describeThisComputer(status({ targets: [theirs] }), null, WORKSPACE, null, "this Mac", "me").label,
        "Connecting",
    );
});

test("a second person signed in on this Mac gets a pairing of their own", async () => {
    /* Named for the platform the shell reports, not the one running the test. */
    page({ shell: () => null, info: { mode: "hosted", platform: "macos" } });
    const log: string[] = [];
    const theirs = status({ targets: [target({ user_id: "them" })] });
    assert.equal(await connectThisComputer(theirs, WORKSPACE, { ...deps(log), userId: "me" }), "connected");
    assert.deepEqual(log, ["mint:My Mac", `pair:${WORKSPACE}:code-1`, "refresh"]);
});

test("only a person's retry asks to turn a removed computer back on", async () => {
    const log: string[] = [];
    const unpaired = status({ targets: [] });
    const removed = "This computer was removed from this account. Connect it again from Lemma to turn it back on.";
    const refusing: ConnectDeps = {
        ...deps(log),
        host: { ...deps(log).host, pair: async () => { throw new Error(removed); } },
    };
    assert.equal(await connectThisComputer(unpaired, WORKSPACE, refusing), "failed");
    assert.equal(wasRemoved(connectFailure()), true);
    retryAutoConnect();
    assert.equal(await connectThisComputer(unpaired, WORKSPACE, deps(log)), "connected");
    assert.ok(log.includes(`pair:${WORKSPACE}:code-1:reenable`), log.join(" "));
    /* Spent by that attempt: the next automatic one does not re-enable. */
    resetAutoConnectForTests();
    await connectThisComputer(unpaired, WORKSPACE, deps(log));
    assert.equal(log.filter((entry) => entry.endsWith(":reenable")).length, 1);
});

test("who is signed in is told once per page, and an old shell's refusal is harmless", async () => {
    const told: (string | null)[] = [];
    const host = { session: async (_url: string, user: string | null) => { told.push(user); throw new Error("unknown command"); } };
    await tellSession(WORKSPACE, "me", host);
    await tellSession(WORKSPACE, "me", host);
    await tellSession(WORKSPACE, null, host);
    assert.deepEqual(told, ["me", null]);
});

/* ── conversation folders ──────────────────────────────────────────── */

test("folders are only offered on a local install in the app", async () => {
    const state = page({ shell: () => "/Users/me/work" });
    assert.equal(await readFolder({ conversationId: null, pendingId: "p" }), null);
    assert.equal(await bindFolder({ conversationId: null, pendingId: "p" }), null);
    await adoptConversationFolder("c", "p");
    assert.deepEqual(state.calls, []);
});

test("a folder chosen while composing is parked, then adopted", async () => {
    const parked = new Map<string, string>();
    const state = page({
        deployment: "local",
        shell: (command, args) => {
            const slot = (args?.conversationId as string | null) ?? "pending:" + String(args?.pendingId);
            if (command === "bind_conversation_folder") { parked.set(slot, "/Users/me/lemma"); return "/Users/me/lemma"; }
            if (command === "conversation_folder") return parked.get(slot) ?? null;
            if (command === "unbind_conversation_folder") { parked.delete(slot); return null; }
            if (command === "adopt_conversation_folder") {
                const waiting = parked.get("pending:" + String(args?.pendingId));
                if (waiting) parked.set(String(args?.conversationId), waiting);
                return null;
            }
            throw new Error("unexpected " + command);
        },
    });
    const composing = { conversationId: null, pendingId: "composer-a" };
    assert.equal(await bindFolder(composing), "/Users/me/lemma");
    assert.equal(await readFolder(composing), "/Users/me/lemma");

    await adoptConversationFolder("conv-1", "composer-a");
    assert.equal(await readFolder({ conversationId: "conv-1", pendingId: "composer-a" }), "/Users/me/lemma");

    await unbindFolder({ conversationId: "conv-1", pendingId: "composer-a" });
    assert.equal(await readFolder({ conversationId: "conv-1", pendingId: "composer-a" }), null);
    assert.deepEqual(state.calls.map((call) => call.command), [
        "bind_conversation_folder", "conversation_folder", "adopt_conversation_folder",
        "conversation_folder", "unbind_conversation_folder", "conversation_folder",
    ]);
});

test("a dismissed dialog binds nothing, and a refusing shell fails nothing", async () => {
    page({ deployment: "local", shell: (command) => {
        if (command === "bind_conversation_folder") return null;
        throw new Error("refused");
    } });
    assert.equal(await bindFolder({ conversationId: null, pendingId: "p" }), null);
    assert.equal(await readFolder({ conversationId: null, pendingId: "p" }), null);
    /* The first message must still go if adoption fails. */
    await adoptConversationFolder("conv-1", "p");
});

test("a folder chip shows the last segment of either kind of path", () => {
    assert.equal(folderLabel("/Users/me/projects/lemma"), "lemma");
    assert.equal(folderLabel("C:\\Users\\me\\lemma"), "lemma");
    assert.equal(folderLabel("/Users/me/lemma/"), "lemma");
    assert.equal(folderLabel("/"), "/");
});

/* ── sandbox images ────────────────────────────────────────────────── */

test("a sandbox that was already warm says nothing", () => {
    assert.equal(sandboxImageNotice(null, { state: "ready", detail: "" }).kind, "none");
    assert.equal(sandboxImageNotice(null, { state: "failed", detail: "" }).kind, "none");
    assert.equal(sandboxImageNotice(null, { state: "downloading", detail: "" }).kind, "downloading");
    assert.equal(sandboxImageNotice("downloading", { state: "downloading", detail: "" }).kind, "none");
    assert.equal(sandboxImageNotice("downloading", { state: "ready", detail: "" }).kind, "ready");
    assert.equal(sandboxImageNotice("downloading", { state: "failed", detail: "" }).kind, "unavailable");
    assert.equal(shouldKeepPolling("unsupported"), false);
    assert.equal(shouldKeepPolling("downloading"), true);
});

/* ── settings from the shell ───────────────────────────────────────── */

test("the shell can open Settings at a section, and an unknown one opens the first", () => {
    const event = (detail: unknown) => ({ detail }) as unknown as Event;
    assert.equal(requestedSection(event({ section: "models" })), "models");
    assert.equal(requestedSection(event("connectors")), "connectors");
    assert.equal(requestedSection(event({ section: "local-runtime" })), "account");
    assert.equal(requestedSection(event(null)), "account");
});

/* ── hosted sign-in in the system browser ──────────────────────────── */

test("only a hosted workspace in the app signs in through the browser", () => {
    page();
    assert.equal(shouldUseBrowserHandoff(), false);
    page({ shell: () => null, info: { mode: "local" } });
    assert.equal(shouldUseBrowserHandoff(), false);
    page({ shell: () => null, info: { mode: "hosted" } });
    assert.equal(shouldUseBrowserHandoff(), true);
});

test("the browser URL carries the marker the shell hands out", () => {
    const id = "a".repeat(24);
    const url = new URL(browserSignInUrl("https://lemma.work", "/auth", id, true));
    assert.equal(url.pathname, "/auth/signup");
    assert.equal(url.searchParams.get("desktop_browser"), "1");
    assert.equal(requestIdFromSearch(url.search), id);
    assert.equal(requestIdFromSearch("?desktop_request=short"), null, "a malformed id is ignored");
});
