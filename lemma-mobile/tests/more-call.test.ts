import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as callPage from "../src/areas/more/call/call-page.generated";
import { CallMachine, LOAD_MS, type CallHandlers } from "../src/areas/more/call/machine";
import {
    afterWords,
    callLine,
    callWords,
    captionOf,
    configuredFrom,
    deckWhere,
    DRAIN,
    drainStep,
    microphoneOff,
    NOT_SET_UP,
    pageProblem,
    parseFromPage,
    phaseOf,
    scriptFor,
    shownIndex,
    spokenAfter,
    voiceProviderOf,
    type DrainClock,
    type FromPage,
    type ToPage,
} from "../src/areas/more/call/model";
import { defaultAgentOf, humanizeName, profileFrom } from "../src/areas/more/call/profile";
import { SampleCallSession, scriptedVoice } from "../src/areas/more/call/sample";
import type { EngineLink } from "../src/areas/more/call/source";
import { podBrief } from "@web/call/pod-brief";
import type { VoiceEvent } from "@web/call/routing";
import { buildTurns } from "@web/thread/turns";

/** A voice call (PARITY.md, G): the web's call modules copied here, the page
 *  that holds the microphone, the protocol between them, and what the phone
 *  decides and says on top. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
/** The stamp `webview/build.mjs` writes: every input, hashed in order. */
function stamp(inputs: string[]): string {
    const hash = createHash("sha256");
    for (const file of [...inputs].sort()) {
        hash.update(file + "\0");
        hash.update(readFileSync(mobile + file));
    }
    return hash.digest("hex").slice(0, 16);
}

describe("the call's page", () => {
    const page = readFileSync(mobile + "webview/src/call/main.ts", "utf8");

    it.runIf(callPage.INPUTS.every((file) => existsSync(mobile + file)))("is built from its sources as they are (run npm run build -- call in webview/ if not)", () => {
        expect(stamp(callPage.INPUTS)).toBe(callPage.STAMP);
    });

    it("runs the web's own voice clients and audio", () => {
        for (const file of ["../lemma-frontend/src/call/gpt-live-client.ts", "../lemma-frontend/src/call/gemini-live-client.ts", "../lemma-frontend/src/call/pcm-audio.ts"]) {
            expect(callPage.INPUTS).toContain(file);
        }
    });

    it("holds no session and no address of the API", () => {
        expect(callPage.HTML).not.toMatch(/api\.lemma\.work|localhost:8761/);
        expect(callPage.HTML).not.toMatch(/Authorization|Bearer |access_token|st-access-token/);
        expect(page).not.toMatch(/session\/|freshAccess|currentTokens/);
    });

    it("answers every message the app sends, and nothing else", () => {
        const handled = [...page.matchAll(/case "([a-z-]+)":/g)].map((match) => match[1]).sort();
        const sent: ToPage["type"][] = ["start", "history", "listen", "reply", "widget", "mute", "clear", "end"];
        expect(handled).toEqual([...sent].sort());
    });

    it("says only what the app reads", () => {
        const posted = [...new Set([...page.matchAll(/post\(\{ type: "([a-z-]+)"/g)].map((match) => match[1]))].sort();
        expect(posted).toEqual(["closed", "error", "interrupted", "level", "listening", "open", "playing", "ready", "spoke", "thinking", "transcript", "turn-complete"]);
        const samples: Record<string, unknown> = {
            closed: { reason: "Call ended" }, error: { message: "x" }, level: { value: 0.5 }, playing: { on: true },
            thinking: { on: false }, transcript: { speaker: "them", text: "hi", final: true },
        };
        for (const type of posted) expect(parseFromPage(JSON.stringify({ type, ...(samples[type] as object) }))?.type).toBe(type);
    });
});

describe("the protocol", () => {
    it("reads the page's messages and drops anything else", () => {
        expect(parseFromPage('{"type":"transcript","speaker":"us","text":"Hello","final":true}')).toEqual({ type: "transcript", speaker: "us", text: "Hello", final: true });
        expect(parseFromPage('{"type":"transcript","speaker":"us","text":"Hel"}')).toEqual({ type: "transcript", speaker: "us", text: "Hel", final: false });
        expect(parseFromPage('{"type":"transcript","speaker":"bot","text":"x"}')).toBeNull();
        expect(parseFromPage('{"type":"level","value":3}')).toEqual({ type: "level", value: 1 });
        expect(parseFromPage('{"type":"level","value":"loud"}')).toBeNull();
        expect(parseFromPage('{"type":"playing","on":"yes"}')).toEqual({ type: "playing", on: false });
        expect(parseFromPage('{"type":"error","message":"No mic","cause":"microphone"}')).toEqual({ type: "error", message: "No mic", cause: "microphone" });
        expect(parseFromPage('{"type":"error","message":"x","cause":"gremlins"}')).toEqual({ type: "error", message: "x" });
        expect(parseFromPage('{"type":"audio","data":"AAAA"}')).toBeNull();
        expect(parseFromPage("not json")).toBeNull();
        expect(parseFromPage("null")).toBeNull();
    });

    it("hands a message to the page as a call it can run", () => {
        const message: ToPage = { type: "reply", text: "Say “this”\nthen </script> that", id: "d1" };
        const script = scriptFor(message);
        expect(script.startsWith("window.__fromApp && window.__fromApp(")).toBe(true);
        const received: ToPage[] = [];
        new Function("window", script)({ __fromApp: (one: ToPage) => received.push(one) });
        expect(received).toEqual([message]);
    });
});

describe("before a call", () => {
    it("asks the web server whether it can carry one", () => {
        expect(configuredFrom({ configured: true })).toBe(true);
        expect(configuredFrom({ configured: "true" })).toBe(false);
        expect(configuredFrom({})).toBe(false);
        expect(configuredFrom(null)).toBe(false);
        expect(NOT_SET_UP).toBe("Voice calls aren’t set up on this server.");
    });

    it("reads the voice model from site-config.js, Gemini unless it says gpt-live", () => {
        expect(voiceProviderOf('window.__LEMMA_SITE__={"apiUrl":"https://api.example.com","voiceProvider":"gpt-live"};')).toBe("gpt-live");
        expect(voiceProviderOf('window.__LEMMA_SITE__={"voiceProvider":"gemini"};')).toBe("gemini");
        expect(voiceProviderOf('window.__LEMMA_SITE__={"voiceProvider":""};')).toBe("gemini");
        expect(voiceProviderOf('window.__LEMMA_SITE__={"features":"contacts"}')).toBe("gemini");
        expect(voiceProviderOf("window.__LEMMA_SITE__={broken};")).toBe("gemini");
        expect(voiceProviderOf("<html>Not found</html>")).toBe("gemini");
        expect(voiceProviderOf(null)).toBe("gemini");
        /* The web escapes `<` in what it serves (`siteRuntimeScript`). */
        expect(voiceProviderOf('window.__LEMMA_SITE__={"siteUrl":"\\u003chttps://example.com","voiceProvider":"gpt-live"};')).toBe("gpt-live");
    });
});

describe("the drain: when the voice may speak", () => {
    const quiet: DrainClock = { now: 100_000, lastUser: 0, lastVoice: 0, providerBusy: 0, awaitingAudio: 0, playing: false };

    it("goes at a real pause", () => {
        expect(drainStep(quiet, false)).toEqual({ kind: "go", clear: false });
    });

    it("waits while the voice is busy, awaited or playing, and for a moment of quiet", () => {
        const wait = { kind: "wait", ms: DRAIN.RETRY_MS };
        expect(drainStep({ ...quiet, providerBusy: quiet.now - 7_999 }, false)).toEqual(wait);
        expect(drainStep({ ...quiet, providerBusy: quiet.now - 8_000 }, false)).toEqual({ kind: "go", clear: false });
        expect(drainStep({ ...quiet, awaitingAudio: quiet.now - 14_999 }, false)).toEqual(wait);
        expect(drainStep({ ...quiet, awaitingAudio: quiet.now - 15_000 }, false)).toEqual({ kind: "go", clear: false });
        expect(drainStep({ ...quiet, playing: true }, false)).toEqual(wait);
        expect(drainStep({ ...quiet, lastUser: quiet.now - 1_199 }, false)).toEqual(wait);
        expect(drainStep({ ...quiet, lastVoice: quiet.now - 1_199 }, false)).toEqual(wait);
        expect(drainStep({ ...quiet, lastVoice: quiet.now - 1_200 }, false)).toEqual({ kind: "go", clear: false });
    });

    it("lets a clarification wait only for the caller, then take the floor", () => {
        expect(drainStep({ ...quiet, lastUser: quiet.now - 499, playing: true }, true)).toEqual({ kind: "wait", ms: DRAIN.FLOOR_RETRY_MS });
        expect(drainStep({ ...quiet, lastUser: quiet.now - 500, playing: true, providerBusy: quiet.now }, true)).toEqual({ kind: "go", clear: true });
    });

    it("writes the spoken exchange as the router reads it", () => {
        let spoken = { history: "", lastSpeaker: null as "them" | "us" | null };
        spoken = spokenAfter(spoken, { speaker: "them", text: "How many" });
        spoken = spokenAfter(spoken, { speaker: "them", text: " are waiting?", final: true });
        spoken = spokenAfter(spoken, { speaker: "us", text: "Let me check." });
        spoken = spokenAfter(spoken, { speaker: "us", text: "", final: true });
        spoken = spokenAfter(spoken, { speaker: "us", text: "One." });
        expect(spoken.history).toBe("\nUser: How many are waiting?\nVoice: Let me check.\nVoice: One.");
        expect(spokenAfter({ history: "x".repeat(90_000), lastSpeaker: "us" }, { speaker: "us", text: "yz" }).history).toHaveLength(90_000);
    });
});

describe("what the screen says", () => {
    it("says each phase plainly", () => {
        expect(callWords({ kind: "checking" }, "Support")).toEqual({ title: "Connecting to Support…", line: "", tone: "quiet" });
        expect(callWords({ kind: "unconfigured" }, "Support")).toEqual({ title: "Support", line: "Voice calls aren’t set up on this server.", tone: "quiet" });
        expect(callWords({ kind: "asking" }, "Support").line).toBe("So Support can hear you, the app needs the microphone.");
        expect(callWords({ kind: "refused", final: true }, "Support", {}, "android")).toEqual({
            title: "Support can’t hear you",
            line: "The app doesn’t have the microphone. Turn it on in Settings, Apps, Lemma, Permissions, Microphone, then call again.",
            tone: "bad",
        });
        expect(callWords({ kind: "refused", final: false }, "Support", {}, "android").line).toContain("Allow it when the phone asks");
        expect(callWords({ kind: "connecting" }, "Support")).toEqual({ title: "Connecting to Support…", line: "", tone: "quiet" });
        expect(callWords({ kind: "connecting" }, "Support", { muted: true }).line).toBe("Muted. It cannot hear you.");
        expect(callWords({ kind: "live" }, "Support")).toEqual({ title: "On a call with Support", line: "Just talk. It is listening.", tone: "quiet" });
        expect(callWords({ kind: "ended" }, "Support").title).toBe("Call ended");
        expect(callWords({ kind: "failed", error: "Could not connect to the voice server." }, "Support")).toEqual({ title: "The call stopped", line: "Could not connect to the voice server.", tone: "bad" });
    });

    it("moves the status line through working and done, without the web's dashes", () => {
        const plan = (a: string, b: string) => [{ step: "Reading the tickets table", status: a }, { step: "Counting what is waiting", status: b }] as never;
        expect(callLine({ teammate: "Support", thinking: true })).toBe("Support is still working. You can keep talking.");
        expect(callLine({ teammate: "Support", thinking: true, plan: plan("in_progress", "pending") })).toBe("Step 1 of 2 · Reading the tickets table");
        expect(callLine({ teammate: "Support", thinking: true, plan: plan("completed", "in_progress") })).toBe("Step 2 of 2 · Counting what is waiting");
        expect(callLine({ teammate: "Support", thinking: false, plan: plan("completed", "completed") })).toBe("Just talk. It is listening.");
        expect(callLine({ teammate: "Support", muted: true, thinking: true })).toBe("Muted. It cannot hear you.");
        expect(callLine({ teammate: "Support", error: "The call dropped.", muted: true })).toBe("The call dropped.");
        for (const line of [callLine({ teammate: "A", muted: true }), callLine({ teammate: "A", thinking: true })]) expect(line).not.toContain("—");
    });

    it("tells phases apart from the setup and the call", () => {
        expect(phaseOf({ kind: "opening" }, "idle", null)).toEqual({ kind: "connecting" });
        expect(phaseOf({ kind: "started" }, "connecting", null)).toEqual({ kind: "connecting" });
        expect(phaseOf({ kind: "started" }, "live", null)).toEqual({ kind: "live" });
        expect(phaseOf({ kind: "started" }, "ended", null)).toEqual({ kind: "ended" });
        expect(phaseOf({ kind: "started" }, "error", "Voice connection timed out.")).toEqual({ kind: "failed", error: "Voice connection timed out." });
        expect(phaseOf({ kind: "started" }, "error", "x", "microphone")).toEqual({ kind: "refused", final: true });
        expect(phaseOf({ kind: "unconfigured" }, "idle", null)).toEqual({ kind: "unconfigured" });
        expect(phaseOf({ kind: "cancelled" }, "idle", null)).toEqual({ kind: "ended" });
    });

    it("says why the page stopped", () => {
        expect(pageProblem("NotAllowedError", "microphone", "ios")).toBe(microphoneOff("ios"));
        expect(microphoneOff("ios")).toContain("Settings, Lemma, Microphone");
        expect(pageProblem("", "insecure", "android")).toContain("secure (https) address");
        expect(pageProblem("Voice connection timed out.", "voice", "android")).toBe("Voice connection timed out.");
        expect(pageProblem("  ", undefined, "android")).toBe("The call dropped.");
    });

    it("captions the line being said, its end when it runs long", () => {
        expect(captionOf([], "Support")).toBeNull();
        expect(captionOf([{ id: 1, speaker: "them", text: "Hi", done: true }, { id: 2, speaker: "us", text: "Hello there.", done: false }], "Support")).toEqual({ who: "Support", text: "Hello there." });
        expect(captionOf([{ id: 1, speaker: "them", text: "Hi", done: true }], "Support")?.who).toBe("You");
        const long = captionOf([{ id: 1, speaker: "us", text: "word ".repeat(60) + "end.", done: false }], "Support", 40);
        expect(long?.text.startsWith("…")).toBe(true);
        expect(long?.text.endsWith("end.")).toBe(true);
        expect(long?.text.length).toBeLessThanOrEqual(40);
    });

    it("says where the call's chat is", () => {
        expect(afterWords({ id: "c1", title: "Tickets still waiting" })).toEqual({ line: "What was asked and done is in “Tickets still waiting”.", link: "Open the chat" });
        expect(afterWords({ id: "c1", title: null })).toEqual({ line: "What was asked and done is in the call’s chat.", link: "Open the chat" });
        expect(afterWords(null)).toEqual({ line: "Nothing from this call went to a chat.", link: null });
    });

    it("pages what was shown, newest on screen", () => {
        expect(deckWhere(2, 3)).toBe("3 of 3");
        expect(shownIndex(5, 3)).toBe(2);
        expect(shownIndex(0, 0)).toBe(0);
    });

    it("never says pod, teammate, agent or an exclamation", () => {
        const phases = [{ kind: "checking" }, { kind: "unconfigured" }, { kind: "asking" }, { kind: "refused", final: true }, { kind: "refused", final: false }, { kind: "connecting" }, { kind: "live" }, { kind: "ended" }] as const;
        const said = [
            ...phases.flatMap((phase) => Object.values(callWords(phase, "Support", { muted: phase.kind === "live", thinking: true }))),
            afterWords(null).line, afterWords({ id: "x", title: null }).line, pageProblem("", "insecure", "ios"), microphoneOff("android", false),
        ].join(" ");
        expect(said).not.toMatch(/\b(pod|teammate|agent|bot|assistant)s?\b|!|—/i);
    });
});

describe("the Lemma, as the voice is told", () => {
    it("builds the brief from the reads the web's profile makes", () => {
        const agents = { items: [{ name: "researcher", kind: "AGENT" }, { name: "pod_default", kind: "POD_DEFAULT" }] };
        expect(defaultAgentOf(agents)).toBe("pod_default");
        expect(defaultAgentOf({ items: [{ name: "researcher" }] })).toBeNull();
        const profile = profileFrom({
            podId: "p1",
            pod: { name: "support-desk", description: "Answers customers." },
            agents,
            defaultAgent: { instruction: "Answer every customer within a day.", toolsets: [] },
            schedules: { items: [{ id: "s1", name: "morning-digest", is_active: true, type: "TIME", config: { cron: "0 9 * * 1-5" } }] },
            apps: { items: [{ id: "a1", name: "ticket-queue" }] },
            tables: { items: [{ name: "tickets" }, { name: "customers" }] },
            functions: null,
            workflows: [],
        });
        expect(profile.name).toBe("Support desk");
        expect(profile.unavailable).toEqual(["functions"]);
        expect(profile.skills.length).toBeGreaterThan(3);
        const brief = podBrief(profile, "Support");
        expect(brief).toContain('This pod is "Support desk"');
        expect(brief).toContain("What it is for: Answers customers.");
        expect(brief).toContain("How it describes itself: Answer every customer within a day.");
        expect(brief).toContain("It holds 2 data tables.");
        expect(brief).toContain("Apps it has built and runs: Ticket queue.");
        expect(brief).toMatch(/Standing work that runs without being asked: Morning digest \(/);
    });

    it("is still a brief when nothing could be read", () => {
        const profile = profileFrom({ podId: "p1", pod: null, agents: null, defaultAgent: null, schedules: null, apps: null, tables: null, functions: null, workflows: null });
        expect(profile.unavailable).toEqual(["tools", "schedules", "apps", "tables", "functions", "workflows"]);
        expect(podBrief(profile, "Support")).toContain("you are the voice of Support");
        expect(humanizeName("dailyCompetitor-refresh")).toBe("Daily Competitor refresh");
    });
});

/* ── the call itself, on a clock ── */

function recorder() {
    const posted: ToPage[] = [];
    let hear: ((message: FromPage) => void) | null = null;
    const engine: EngineLink = {
        open: (listener) => { hear = listener; },
        post: (message) => { posted.push(message); },
        close: () => { hear = null; },
    };
    return { engine, posted, say: (message: FromPage) => hear?.(message), types: () => posted.map((one) => one.type) };
}

function handlersFor() {
    const routed: { id: string; text: string; transcript: string }[] = [];
    let listener: ((event: VoiceEvent) => void) | null = null;
    const handlers: CallHandlers = {
        observeTranscript: vi.fn(),
        route: async (id, text, transcript) => { routed.push({ id, text, transcript }); },
        subscribe: (heard) => { listener = heard; return () => { listener = null; }; },
        history: () => "Them: Where are we?",
        context: async () => "This pod is \"Support\".",
    };
    return { handlers, routed, emit: (event: VoiceEvent) => listener?.(event) };
}

describe("a call", () => {
    beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_000_000); });
    afterEach(() => { vi.useRealTimers(); });

    async function placed() {
        const engine = recorder();
        const handlers = handlersFor();
        const call = new CallMachine({ handlers: () => handlers.handlers, engine: engine.engine, os: "android" });
        void call.start("gpt-live", { lemma: "Support", place: "Support" });
        await vi.advanceTimersByTimeAsync(0);
        engine.say({ type: "ready" });
        engine.say({ type: "open" });
        await vi.advanceTimersByTimeAsync(0);
        return { call, ...engine, ...handlers };
    }

    it("starts on the page's word, briefs the voice, then opens the microphone", async () => {
        const { call, posted, types } = await placed();
        expect(posted[0]).toMatchObject({ type: "start", provider: "gpt-live" });
        expect((posted[0] as { instructions: string }).instructions).toContain('You are the live voice of "Support" in "Support"');
        expect((posted[0] as { instructions: string }).instructions).toContain('This pod is "Support".');
        expect(types()).toEqual(["start", "history", "listen"]);
        expect(call.snapshot().status).toBe("live");
    });

    it("waits for the page before it sends anything", async () => {
        const engine = recorder();
        const call = new CallMachine({ handlers: () => handlersFor().handlers, engine: engine.engine });
        void call.start("gemini", { lemma: "Support", place: "Support" });
        await vi.advanceTimersByTimeAsync(0);
        expect(engine.posted).toEqual([]);
        expect(call.snapshot().status).toBe("connecting");
        engine.say({ type: "ready" });
        expect(engine.types()).toEqual(["start"]);
    });

    it("gives up on a page that never loads", async () => {
        const engine = recorder();
        const call = new CallMachine({ handlers: () => handlersFor().handlers, engine: engine.engine });
        void call.start("gemini", { lemma: "Support", place: "Support" });
        await vi.advanceTimersByTimeAsync(LOAD_MS);
        expect(call.snapshot()).toMatchObject({ status: "error", error: "The call’s sound didn’t start. Try calling again." });
    });

    it("routes what the caller finished saying, once", async () => {
        const { say, routed } = await placed();
        say({ type: "transcript", speaker: "them", text: "How many tickets", final: false });
        say({ type: "transcript", speaker: "them", text: " are waiting?", final: true });
        await vi.advanceTimersByTimeAsync(349);
        expect(routed).toEqual([]);
        await vi.advanceTimersByTimeAsync(1);
        expect(routed).toHaveLength(1);
        expect(routed[0].text).toBe("How many tickets are waiting?");
        expect(routed[0].transcript).toBe("\nUser: How many tickets are waiting?");
    });

    it("keeps quiet updates quiet and says an answer at the next pause", async () => {
        const { say, routed, emit, posted, types } = await placed();
        say({ type: "transcript", speaker: "them", text: "How many are waiting?", final: true });
        await vi.advanceTimersByTimeAsync(350);
        const asked = routed[0].id;
        posted.length = 0;
        emit({ id: "e1", conversationId: "c1", kind: "accepted", speak: false, text: "Work accepted." });
        await vi.advanceTimersByTimeAsync(2000);
        expect(posted).toEqual([{ type: "history", text: "Work accepted." }]);

        /* The voice acknowledges; the answer waits for it to finish. */
        say({ type: "playing", on: true });
        say({ type: "spoke" });
        emit({ id: "e2", conversationId: "c1", kind: "completed", speak: true, responseTo: asked, text: "One is waiting." });
        await vi.advanceTimersByTimeAsync(3000);
        expect(types()).toEqual(["history"]);
        say({ type: "playing", on: false });
        say({ type: "turn-complete" });
        await vi.advanceTimersByTimeAsync(DRAIN.QUIET_MS + DRAIN.RETRY_MS);
        const reply = posted.find((one) => one.type === "reply") as { text: string } | undefined;
        expect(reply?.text).toContain("One is waiting.");
    });

    it("lets a clarification cut in", async () => {
        const { say, routed, emit, types } = await placed();
        say({ type: "transcript", speaker: "them", text: "Send it.", final: true });
        await vi.advanceTimersByTimeAsync(350);
        say({ type: "playing", on: true });
        emit({ id: "q1", conversationId: null, kind: "clarify", speak: true, text: "Which one?" });
        emit({ id: routed[0].id, conversationId: null, kind: "clarify", speak: true, text: "Which one?" });
        await vi.advanceTimersByTimeAsync(400);
        expect(types().slice(-2)).toEqual(["clear", "reply"]);
    });

    it("mutes, and ends", async () => {
        const { call, posted, types } = await placed();
        call.toggleMute();
        expect(posted.at(-1)).toEqual({ type: "mute", on: true });
        expect(call.snapshot().muted).toBe(true);
        call.toggleMute();
        expect(posted.at(-1)).toEqual({ type: "mute", on: false });
        call.end();
        expect(types().at(-1)).toBe("end");
        expect(call.snapshot()).toMatchObject({ status: "ended", muted: false, transcript: [] });
        call.end();
        expect(types().filter((one) => one === "end")).toHaveLength(1);
    });

    it("says what stopped it", async () => {
        const { call, say } = await placed();
        say({ type: "error", message: "Permission denied", cause: "microphone" });
        expect(call.snapshot()).toMatchObject({ status: "error", cause: "microphone", error: microphoneOff("android") });
        call.end();
        expect(call.snapshot().status).toBe("error");
    });

    it("keeps a mute pressed before the page was ready", async () => {
        const engine = recorder();
        const call = new CallMachine({ handlers: () => handlersFor().handlers, engine: engine.engine });
        void call.start("gemini", { lemma: "Support", place: "Support" });
        await vi.advanceTimersByTimeAsync(0);
        call.toggleMute();
        engine.say({ type: "ready" });
        expect(engine.posted.slice(-2)).toEqual([expect.objectContaining({ type: "start" }), { type: "mute", on: true }]);
    });

    it("moves the ring without redrawing the call", async () => {
        const { call, say } = await placed();
        const view = call.snapshot();
        let rings = 0;
        call.subscribeLevel(() => { rings += 1; });
        say({ type: "level", value: 0.6 });
        say({ type: "level", value: 0.6 });
        say({ type: "level", value: 0.2 });
        expect(call.levelSnapshot()).toBe(0.2);
        expect(rings).toBe(2);
        expect(call.snapshot()).toBe(view);
        call.end();
        expect(call.levelSnapshot()).toBe(0);
    });

    it("ignores an old call's words", async () => {
        const { call, say } = await placed();
        call.end();
        say({ type: "transcript", speaker: "us", text: "late", final: true });
        expect(call.snapshot().transcript).toEqual([]);
    });
});

describe("a sample call", () => {
    beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_000_000); });
    afterEach(() => { vi.useRealTimers(); });

    it("connects, talks both ways, works, shows one thing, answers, mutes and ends", async () => {
        const session = new SampleCallSession("sample-support-1140", () => undefined);
        const voice = scriptedVoice("sample-support-1140");
        const call = new CallMachine({
            handlers: () => ({ observeTranscript: session.observeTranscript, route: session.route, subscribe: session.subscribe, context: async () => "" }),
            engine: voice,
        });
        const seen = new Set<string>();
        const lines = new Set<string>();
        call.subscribe(() => {
            const view = call.snapshot();
            seen.add(view.status);
            const last = view.transcript.at(-1);
            if (last?.done) lines.add(`${last.speaker}: ${last.text}`);
        });
        const status = () => {
            const items = buildTurns([...(session.messages.get(session.focusedId ?? "") ?? [])]).flatMap((turn) => turn.items);
            const plan = items.reduce<never[]>((found, item) => (item.kind === "plan" ? (item.steps as never[]) : found), []);
            return { line: callLine({ teammate: "Support", plan, thinking: session.isRunning }), shown: items.filter((item) => item.kind === "resource").length };
        };

        const opened = session.open(null);
        await vi.advanceTimersByTimeAsync(300);
        expect(await opened).toBeNull();
        void call.start("gemini", { lemma: "Support", place: "Support" });
        await vi.advanceTimersByTimeAsync(1500);
        expect(call.snapshot().status).toBe("live");

        const along: string[] = [];
        for (let step = 0; step < 120; step += 1) {
            await vi.advanceTimersByTimeAsync(100);
            const now = status().line;
            if (along.at(-1) !== now) along.push(now);
        }
        expect(seen.has("connecting")).toBe(true);
        expect([...lines]).toEqual(expect.arrayContaining([
            "us: I’m here. What should we look at?",
            "them: How many tickets are still waiting on us?",
            "us: Let me check the queue.",
            "us: Just one. Orchard Lane, about missing screws. The tickets are on your screen.",
        ]));
        expect(along).toEqual(expect.arrayContaining([
            "Support is still working. You can keep talking.",
            "Step 1 of 2 · Reading the tickets table",
            "Step 2 of 2 · Counting what is waiting",
        ]));
        expect(along.at(-1)).toBe("Just talk. It is listening.");
        expect(status().shown).toBe(1);
        expect(session.focusedId).toBe("call-sample-support-1140");
        expect(session.snapshots.get(session.focusedId ?? "")?.title).toBe("Tickets still waiting");

        call.toggleMute();
        expect(call.snapshot().muted).toBe(true);
        call.end();
        expect(call.snapshot().status).toBe("ended");
    });

    it("holds the question while muted", async () => {
        const voice = scriptedVoice("sample-books-986");
        const heard: FromPage[] = [];
        voice.open((message) => heard.push(message));
        voice.post({ type: "start", provider: "gemini", instructions: "" });
        voice.post({ type: "listen" });
        voice.post({ type: "mute", on: true });
        await vi.advanceTimersByTimeAsync(8000);
        const said = (speaker: string) => heard.filter((one) => one.type === "transcript" && one.speaker === speaker).length;
        expect(said("us")).toBeGreaterThan(0);
        expect(said("them")).toBe(0);
        voice.post({ type: "mute", on: false });
        await vi.advanceTimersByTimeAsync(3000);
        expect(said("them")).toBeGreaterThan(0);
        voice.close();
    });
});
