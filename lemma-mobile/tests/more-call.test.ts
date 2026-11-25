import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CallFrame, CallSocketHandlers, CallStart } from "@web/call/call-socket";
import { podBrief } from "@web/call/pod-brief";
import * as callPage from "../src/areas/more/call/call-page.generated";
import { AUDIO_FLUSH_MS, CallSession } from "../src/areas/more/call/machine";
import { MAX_REJOINS, REJOIN_DELAY_MS } from "@web/call/call-rejoin";
import { NOTICE_MS } from "@web/call/call-status";
import { onScreenIndex } from "@web/call/canvas";
import {
    afterWords,
    callLine,
    callPlace,
    callWords,
    captionOf,
    configuredFrom,
    deckWhere,
    fromBase64,
    joined,
    microphoneOff,
    pageProblem,
    parseFromPage,
    phaseOf,
    saidOn,
    samplesOf,
    scriptFor,
    toBase64,
    type ToPage,
} from "../src/areas/more/call/model";
import { defaultAgentOf, humanizeName, profileFrom } from "../src/areas/more/call/profile";
import { callSample, scriptedCall } from "../src/areas/more/call/sample";
import type { CallLink, CallSource } from "../src/areas/more/call/source";

/** A voice call (PARITY.md, G), held by the server: the page that holds the
 *  microphone and the speaker, the protocol between it and the app, the call
 *  as the app drives it over the socket, and what the screen says. */

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

    it("plays and records with the web's own audio", () => {
        expect(callPage.INPUTS).toContain("../lemma-frontend/src/call/pcm-audio.ts");
    });

    it("holds no session, no socket and no address of the API", () => {
        expect(callPage.HTML).not.toMatch(/api\.lemma\.work|localhost:8761/);
        expect(callPage.HTML).not.toMatch(/Authorization|Bearer |access_token|st-access-token|new WebSocket/);
        expect(page).not.toMatch(/session\/|freshAccess|WebSocket/);
    });

    it("answers every message the app sends, and nothing else", () => {
        const handled = [...page.matchAll(/case "([a-z-]+)":/g)].map((match) => match[1]).sort();
        const sent: ToPage["type"][] = ["listen", "voice", "audio", "clear", "mute", "end"];
        expect(handled).toEqual([...sent].sort());
    });

    it("says only what the app reads", () => {
        const posted = [...new Set([...page.matchAll(/post\(\{ type: "([a-z-]+)"/g)].map((match) => match[1]))].sort();
        expect(posted).toEqual(["error", "level", "listening", "mic", "ready"]);
        const samples: Record<string, unknown> = { error: { message: "x" }, level: { value: 0.5 }, mic: { pcm: "AAA=" } };
        for (const type of posted) expect(parseFromPage(JSON.stringify({ type, ...(samples[type] as object) }))?.type).toBe(type);
    });
});

describe("the protocol", () => {
    it("reads the page's messages and drops anything else", () => {
        expect(parseFromPage(JSON.stringify({ type: "level", value: 3 }))).toEqual({ type: "level", value: 1 });
        expect(parseFromPage(JSON.stringify({ type: "mic", pcm: "" }))).toBeNull();
        expect(parseFromPage(JSON.stringify({ type: "error", message: "no", cause: "microphone" }))).toEqual({ type: "error", message: "no", cause: "microphone" });
        expect(parseFromPage(JSON.stringify({ type: "error", message: "no", cause: "elsewhere" }))).toEqual({ type: "error", message: "no" });
        expect(parseFromPage(JSON.stringify({ type: "transcript", text: "hi" }))).toBeNull();
        expect(parseFromPage("not json")).toBeNull();
    });

    it("hands a message to the page as a call it can run", () => {
        expect(scriptFor({ type: "mute", on: true })).toBe('window.__fromApp && window.__fromApp({"type":"mute","on":true});true;');
    });

    it("carries PCM as base64, whole samples only", () => {
        const samples = new Int16Array([0, 1, -1, 32767, -32768, 1234]);
        const text = toBase64(new Uint8Array(samples.buffer));
        expect(Array.from(samplesOf(text))).toEqual(Array.from(samples));
        expect(samplesOf(toBase64(new Uint8Array([1, 2, 3]))).length).toBe(1);
        expect(Array.from(fromBase64(toBase64(new Uint8Array(70000).fill(7))).slice(-2))).toEqual([7, 7]);
        expect(Array.from(joined([new Uint8Array([1, 2]).buffer, new Uint8Array([3]).buffer]))).toEqual([1, 2, 3]);
    });

    it("offers a call only where the server names a voice", () => {
        expect(configuredFrom({ engines: ["gemini_live"] })).toBe(true);
        expect(configuredFrom({ engines: [] })).toBe(false);
        expect(configuredFrom(null)).toBe(false);
    });
});

describe("what the screen says", () => {
    it("says each phase plainly", () => {
        expect(callWords({ kind: "checking" }, "Support")).toEqual({ title: "Connecting to Support…", line: "", tone: "quiet" });
        expect(callWords({ kind: "unconfigured" }, "Support")).toEqual({ title: "Support", line: "Voice calls aren’t set up on this server.", tone: "quiet" });
        expect(callWords({ kind: "asking" }, "Support").line).toBe("So Support can hear you, the app needs the microphone.");
        expect(callWords({ kind: "refused", final: true }, "Support", {}, "android").line).toBe("The app doesn’t have the microphone. Turn it on in Settings, Apps, Lemma, Permissions, Microphone, then call again.");
        expect(callWords({ kind: "connecting" }, "Support", { muted: true }).line).toBe("Muted. It cannot hear you.");
        expect(callWords({ kind: "live" }, "Support")).toEqual({ title: "On a call with Support", line: "Just talk. It is listening.", tone: "quiet" });
        expect(callWords({ kind: "live" }, "Support", { thinking: true }).line).toBe("Support is still working. You can keep talking.");
        expect(callWords({ kind: "failed", error: "The voice is not available right now." }, "Support")).toEqual({ title: "The call stopped", line: "The voice is not available right now.", tone: "bad" });
        for (const line of [callLine({ teammate: "A", muted: true }), callLine({ teammate: "A", thinking: true })]) expect(line).not.toContain("—");
    });

    it("tells phases apart from the setup and the call", () => {
        expect(phaseOf({ kind: "opening" }, "idle", null)).toEqual({ kind: "connecting" });
        expect(phaseOf({ kind: "started" }, "live", null)).toEqual({ kind: "live" });
        expect(phaseOf({ kind: "started" }, "error", "The call dropped.")).toEqual({ kind: "failed", error: "The call dropped." });
        expect(phaseOf({ kind: "started" }, "error", "x", "microphone")).toEqual({ kind: "refused", final: true });
        expect(phaseOf({ kind: "cancelled" }, "idle", null)).toEqual({ kind: "ended" });
    });

    it("says why the page stopped", () => {
        expect(pageProblem("NotAllowedError", "microphone", "ios")).toBe(microphoneOff("ios"));
        expect(pageProblem("", "insecure", "android")).toContain("secure (https) address");
        expect(pageProblem("  ", undefined, "android")).toBe("The call dropped.");
    });

    it("captions the line being said, its end when it runs long", () => {
        expect(captionOf([], "Support")).toBeNull();
        expect(captionOf([{ id: 1, speaker: "them", text: "Hi", done: true }], "Support")?.who).toBe("You");
        const long = captionOf([{ id: 1, speaker: "us", text: "word ".repeat(60) + "end.", done: false }], "Support", 40);
        expect(long?.text.startsWith("…")).toBe(true);
        expect(long?.text.endsWith("end.")).toBe(true);
    });

    it("points at the chat that holds what was said after it", () => {
        expect(afterWords({ chatId: "call-1", fromChat: false, said: true })).toEqual({ line: "What was asked and done is in the call’s chat.", link: "Open the chat" });
        /* Nothing happened: the server takes that call's own chat out of the list. */
        expect(afterWords({ chatId: "call-1", fromChat: false, said: false })).toEqual({ line: "Nothing from this call went to a chat.", link: null });
        expect(afterWords({ chatId: null, fromChat: false, said: false }).link).toBeNull();
        /* Held in the chat it came from: the way there is back. */
        expect(afterWords({ chatId: "chat-1", fromChat: true, said: true })).toEqual({ line: "What was asked and done is in the chat.", link: "Back to the chat" });
        expect(afterWords({ chatId: "chat-1", fromChat: true, said: false }).line).toBe("Nothing from this call went to the chat.");
        expect(deckWhere(2, 3)).toBe("3 of 3");
    });

    it("counts a call as held something once anything was said or shown", () => {
        expect(saidOn({ transcript: [], canvas: [] })).toBe(false);
        expect(saidOn({ transcript: [{ id: 1, speaker: "us", text: "  ", done: true }], canvas: [] })).toBe(false);
        expect(saidOn({ transcript: [{ id: 1, speaker: "us", text: "Hi.", done: true }], canvas: [] })).toBe(true);
        expect(saidOn({ transcript: [], canvas: [{}] })).toBe(true);
    });

    it("holds a call in the chat it came from; calling again picks the last call up", () => {
        expect(callPlace("chat-1", null)).toBe("chat-1");
        expect(callPlace("chat-1", { callId: "call-9", said: true })).toBe("chat-1");
        expect(callPlace(null, { callId: "call-9", said: true })).toBe("call-9");
        /* A call nothing happened in has no chat to go back to: a new one. */
        expect(callPlace(null, { callId: "call-9", said: false })).toBeNull();
        expect(callPlace(null, null)).toBeNull();
    });

    it("puts a notice in the line while it is up, outranking muted", () => {
        expect(callWords({ kind: "live" }, "Support", { muted: true, notice: "The usage limit is reached." }).line).toBe("The usage limit is reached.");
        expect(callWords({ kind: "live" }, "Support", { muted: true, notice: null }).line).toBe("Muted. It cannot hear you.");
    });

    it("never says pod, teammate, agent or an exclamation", () => {
        const phases = [{ kind: "checking" }, { kind: "unconfigured" }, { kind: "asking" }, { kind: "refused", final: true }, { kind: "refused", final: false }, { kind: "connecting" }, { kind: "live" }, { kind: "ended" }] as const;
        const afters = [true, false].flatMap((fromChat) => [true, false].map((said) => afterWords({ chatId: "x", fromChat, said })));
        const said = [
            ...phases.flatMap((phase) => Object.values(callWords(phase, "Support", { muted: phase.kind === "live", thinking: true }))),
            ...afters.flatMap((after) => [after.line, after.link ?? ""]), pageProblem("", "insecure", "ios"), microphoneOff("android", false),
        ].join(" ");
        expect(said).not.toMatch(/\b(pod|teammate|agent|bot|assistant|channel|line|place)s?\b|!|—/i);
    });
});

describe("the Lemma, as the voice is told", () => {
    it("builds the brief from the reads the web's profile makes", () => {
        const agents = { items: [{ name: "researcher", kind: "AGENT" }, { name: "pod_default", kind: "POD_DEFAULT" }] };
        expect(defaultAgentOf(agents)).toBe("pod_default");
        const profile = profileFrom({
            podId: "p1",
            pod: { name: "support-desk", description: "Answers customers." },
            agents,
            defaultAgent: { instruction: "Answer every customer within a day.", toolsets: [] },
            schedules: null,
            apps: { items: [{ id: "a1", name: "ticket-queue" }] },
            tables: { items: [{ name: "tickets" }, { name: "customers" }] },
            functions: null,
            workflows: [],
        });
        const brief = podBrief(profile, "Support");
        expect(brief).toContain("What it is for: Answers customers.");
        expect(brief).toContain("It holds 2 data tables.");
        expect(humanizeName("dailyCompetitor-refresh")).toBe("Daily Competitor refresh");
    });
});

/* ── the call itself ── */

/** A call against a fake socket: every connection is kept, with what it was
 *  started with, and the newest one is the one driven. `failConnect` fails
 *  connections from that one on (1 is the first). */
function harness(options: { sound?: boolean; failConnect?: number } = {}) {
    const posted: ToPage[] = [];
    const sent: Record<string, unknown>[] = [];
    const audio: Int16Array[] = [];
    let handlers: CallSocketHandlers | null = null;
    let closed = 0;
    const connects: { callId: string; start: CallStart }[] = [];
    const link: CallLink = {
        sendAudio: (pcm) => { audio.push(pcm); },
        send: (control) => { sent.push(control); },
        close: () => { closed += 1; handlers?.onClose(null, false); },
    };
    const source: CallSource = {
        sound: options.sound ?? true,
        callable: async () => true,
        open: async () => "call-1",
        brief: async () => "",
        connect: async (_podId, callId, start, heard) => {
            connects.push({ callId, start });
            if (options.failConnect && connects.length >= options.failConnect) throw new Error("The call could not connect.");
            handlers = heard;
            return link;
        },
    };
    const session = new CallSession({ source, podId: "p1", post: (message) => { posted.push(message); }, os: "android" });
    return {
        session,
        posted,
        sent,
        audio,
        connects,
        closed: () => closed,
        frame: (frame: CallFrame) => handlers?.onFrame(frame),
        voice: (bytes: number[]) => handlers?.onAudio(new Uint8Array(bytes).buffer),
        /** The socket closes as `closeOutcome` reads it: `dropped` when nobody chose it. */
        close: (reason: string | null, dropped = false) => handlers?.onClose(reason, dropped),
        types: () => posted.map((one) => one.type),
    };
}

const ITEM = (id: string) => ({ conversation_id: "call-1", tool_call_id: id, tool_name: "display_resource", args: { type: "TABLE", name: id }, result: { ok: true } });

const START = { teammate: "Support", pod: "Support", context: "" };

describe("a call", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it("goes live on the server's word, and opens the microphone once the page is up", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        expect(call.session.snapshot().status).toBe("connecting");
        call.frame({ type: "ready", call_id: "call-1", engine: "gemini_live", sample_rate: 24000 });
        expect(call.session.snapshot().status).toBe("live");
        expect(call.types()).toEqual([]);
        call.session.hear({ type: "ready" });
        expect(call.posted).toEqual([{ type: "listen" }, { type: "voice", rate: 24000 }]);
    });

    it("sends what the microphone hears, unless muted, and says muted to both sides", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        const pcm = toBase64(new Uint8Array(new Int16Array([5, -5]).buffer));
        call.session.hear({ type: "mic", pcm });
        expect(Array.from(call.audio[0])).toEqual([5, -5]);
        call.session.toggleMute();
        call.session.hear({ type: "mic", pcm });
        expect(call.audio.length).toBe(1);
        expect(call.sent).toEqual([{ type: "mute", muted: true }]);
        expect(call.posted).toContainEqual({ type: "mute", on: true });
    });

    it("gathers the voice for one trip to the page, and drops it when the caller cuts in", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.voice([1, 2]);
        call.voice([3, 4]);
        vi.advanceTimersByTime(AUDIO_FLUSH_MS);
        expect(call.posted).toEqual([{ type: "audio", pcm: toBase64(new Uint8Array([1, 2, 3, 4])) }]);
        call.voice([9, 9]);
        call.frame({ type: "clear" });
        vi.advanceTimersByTime(AUDIO_FLUSH_MS);
        expect(call.types()).toEqual(["audio", "clear"]);
    });

    it("keeps what is said, what is working, and each canvas item once", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.frame({ type: "transcript", speaker: "voice", text: "Hello", final: false });
        call.frame({ type: "transcript", speaker: "voice", text: "Hello there.", final: true });
        call.frame({ type: "transcript", speaker: "caller", text: "Hi.", final: true });
        expect(captionOf(call.session.snapshot().transcript, "Support")).toEqual({ who: "You", text: "Hi." });
        expect(call.session.snapshot().transcript.map((line) => line.text)).toEqual(["Hello there.", "Hi."]);
        call.frame({ type: "work", running: true });
        expect(call.session.snapshot().working).toBe(true);
        const item = { conversation_id: "call-1", tool_call_id: "t1", tool_name: "display_resource", args: { type: "TABLE", name: "tickets" }, result: { ok: true } };
        call.frame({ type: "canvas.item", item });
        call.frame({ type: "canvas.item", item, replay: true });
        const canvas = call.session.snapshot().canvas;
        expect(canvas.map((one) => one.id)).toEqual(["t1"]);
        expect(canvas[0].conversationId).toBe("call-1");
    });

    it("shows the newest as it arrives or is replayed; a page turned is told to the voice", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.frame({ type: "canvas.item", item: ITEM("a") });
        call.frame({ type: "canvas.item", item: ITEM("b") });
        /* Arriving is not the person pointing at it: the voice drew it. */
        expect(call.sent).toEqual([]);
        const [first, second] = call.session.snapshot().canvas;
        call.session.show(first);
        expect(onScreenIndex(call.session.snapshot().canvas, call.session.snapshot().showing)).toBe(0);
        /* Back to the newest is a page turned too. */
        call.session.show(second);
        expect(call.sent).toEqual([{ type: "canvas.focus", tool_call_id: "a" }, { type: "canvas.focus", tool_call_id: "b" }]);
        call.session.show(first);
        /* The server replays the canvas and tells the voice the newest is up: the screen agrees. */
        call.frame({ type: "canvas.item", item: ITEM("a"), replay: true });
        expect(call.session.snapshot().showing).toBeNull();
        expect(onScreenIndex(call.session.snapshot().canvas, null)).toBe(1);
    });

    it("says why the server refused or ended it, in the web's words, and ends plainly on an ordinary close", async () => {
        const refused = harness();
        await refused.session.start("call-1", START);
        refused.close("The voice is not available right now.");
        vi.advanceTimersByTime(10_000);
        expect(refused.session.snapshot()).toMatchObject({ status: "error", error: "The voice is not available right now." });
        expect(refused.connects.length).toBe(1);
        expect(refused.types()).toContain("end");

        const ended = harness();
        await ended.session.start("call-1", START);
        ended.close(null);
        expect(ended.session.snapshot().status).toBe("ended");

        const unreachable = harness({ failConnect: 1 });
        await unreachable.session.start("call-1", START);
        expect(unreachable.session.snapshot()).toMatchObject({ status: "error", error: "The call could not connect." });
    });

    it("joins a dropped call again, keeping what is on screen, the microphone and mute", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.session.hear({ type: "ready" });
        call.frame({ type: "ready", call_id: "call-1", engine: "gemini_live", sample_rate: 24000 });
        call.frame({ type: "transcript", speaker: "voice", text: "Here are the tickets.", final: true });
        call.frame({ type: "canvas.item", item: ITEM("t1") });
        call.frame({ type: "work", running: true });
        call.session.toggleMute();
        call.posted.length = 0;

        call.close("The call dropped.", true);
        /* Still a call: on screen what it showed and said, while it joins again. */
        expect(call.session.snapshot()).toMatchObject({ status: "connecting", error: null, working: false, muted: true });
        expect(call.session.snapshot().canvas.map((one) => one.id)).toEqual(["t1"]);
        expect(call.session.snapshot().transcript.map((line) => line.text)).toEqual(["Here are the tickets."]);
        expect(call.session.active).toBe(true);
        /* The voice it had queued goes; the page and its microphone stay. */
        expect(call.types()).toEqual(["clear"]);

        vi.advanceTimersByTime(REJOIN_DELAY_MS - 1);
        expect(call.connects.length).toBe(1);
        await vi.advanceTimersByTimeAsync(1);
        expect(call.connects.map((one) => one.callId)).toEqual(["call-1", "call-1"]);
        /* Muted carries over: the server is told on the new connection. */
        expect(call.connects[1].start).toMatchObject({ teammate: "Support", muted: true });
        call.frame({ type: "ready", call_id: "call-1", engine: "gemini_live", sample_rate: 24000 });
        expect(call.session.snapshot().status).toBe("live");
        /* Not told to listen again: the microphone it has is kept. */
        expect(call.types()).toEqual(["clear", "voice"]);
    });

    it("gives up after the web's number of rejoins, waiting longer each time, and says the drop", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        for (let rejoin = 0; rejoin < MAX_REJOINS; rejoin += 1) {
            call.close("The call dropped.", true);
            await vi.advanceTimersByTimeAsync(REJOIN_DELAY_MS * 2 ** rejoin);
            expect(call.connects.length).toBe(rejoin + 2);
        }
        call.close("The call dropped.", true);
        await vi.advanceTimersByTimeAsync(60_000);
        expect(call.connects.length).toBe(MAX_REJOINS + 1);
        expect(call.session.snapshot()).toMatchObject({ status: "error", error: "The call dropped." });
        expect(call.types()).toContain("end");
    });

    it("counts a rejoin that cannot connect as one more drop", async () => {
        const call = harness({ failConnect: 2 });
        await call.session.start("call-1", START);
        call.close("The call dropped.", true);
        await vi.advanceTimersByTimeAsync(60_000);
        expect(call.connects.length).toBe(MAX_REJOINS + 1);
        expect(call.session.snapshot()).toMatchObject({ status: "error", error: "The call could not connect." });
    });

    it("ends while waiting to rejoin, and does not join again", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.close("The call dropped.", true);
        call.session.end();
        expect(call.session.snapshot().status).toBe("ended");
        await vi.advanceTimersByTimeAsync(60_000);
        expect(call.connects.length).toBe(1);
    });

    it("shows a notice for a while, and the call stays up", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.frame({ type: "ready", call_id: "call-1", engine: "gemini_live", sample_rate: 24000 });
        call.frame({ type: "notice", text: "The usage limit is reached." });
        expect(call.session.snapshot()).toMatchObject({ status: "live", notice: "The usage limit is reached." });
        vi.advanceTimersByTime(NOTICE_MS - 1);
        call.frame({ type: "notice", text: "Still reached." });
        vi.advanceTimersByTime(NOTICE_MS - 1);
        expect(call.session.snapshot().notice).toBe("Still reached.");
        vi.advanceTimersByTime(1);
        expect(call.session.snapshot()).toMatchObject({ status: "live", notice: null });
    });

    it("hears a refused microphone as a refusal", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.session.hear({ type: "error", message: "Permission denied", cause: "microphone" });
        const view = call.session.snapshot();
        expect(view.cause).toBe("microphone");
        expect(phaseOf({ kind: "started" }, view.status, view.error, view.cause)).toEqual({ kind: "refused", final: true });
        expect(call.closed()).toBe(1);
    });

    it("ends: closes the socket, tells the page, and ignores what the old call says after", async () => {
        const call = harness();
        await call.session.start("call-1", START);
        call.session.end();
        expect(call.closed()).toBe(1);
        expect(call.session.snapshot().status).toBe("ended");
        expect(call.types()).toContain("end");
        call.frame({ type: "transcript", speaker: "voice", text: "Late.", final: true });
        expect(call.session.snapshot().transcript).toEqual([]);
    });

    it("moves the ring with the page's level, and with the words where there is no sound", async () => {
        const call = harness();
        const seen: number[] = [];
        call.session.subscribeLevel(() => seen.push(call.session.levelSnapshot()));
        await call.session.start("call-1", START);
        call.session.hear({ type: "level", value: 0.4 });
        expect(seen).toEqual([0.4]);

        const quiet = harness({ sound: false });
        await quiet.session.start("call-1", START);
        quiet.frame({ type: "transcript", speaker: "voice", text: "Hi", final: false });
        expect(quiet.session.levelSnapshot()).toBeGreaterThan(0);
        vi.advanceTimersByTime(500);
        expect(quiet.session.levelSnapshot()).toBe(0);
    });
});

describe("a sample call", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    async function opened(source: CallSource, podId: string, conversationId: string | null = null): Promise<string> {
        const opening = source.open(podId, "Support", conversationId);
        await vi.advanceTimersByTimeAsync(300);
        return opening;
    }

    it("connects, talks both ways, works, shows one thing, drops and rejoins, and answers, with no server", async () => {
        const source = callSample();
        expect(source.sound).toBe(false);
        expect(await source.callable("sample-support-1140")).toBe(true);
        const callId = await opened(source, "sample-support-1140");
        const session = new CallSession({ source, podId: "sample-support-1140", post: () => undefined, os: "android" });
        const statuses: string[] = [];
        session.subscribe(() => { if (statuses.at(-1) !== session.snapshot().status) statuses.push(session.snapshot().status); });
        await session.start(callId, START);
        await vi.advanceTimersByTimeAsync(800);
        expect(session.snapshot().status).toBe("live");
        await vi.advanceTimersByTimeAsync(20000);
        /* The drop rejoined the same call: live, connecting again, live. */
        expect(statuses).toEqual(["connecting", "live", "connecting", "live"]);
        const view = session.snapshot();
        expect(view.transcript.map((line) => line.speaker)).toEqual(["us", "them", "us", "us"]);
        expect(view.transcript[1].text).toBe("How many tickets are still waiting on us?");
        /* Replayed after the rejoin onto the same item, not drawn twice. */
        expect(view.canvas.map((one) => one.resource)).toEqual([expect.objectContaining({ type: "TABLE", name: "tickets" })]);
        expect(view.working).toBe(false);
        session.end();
        expect(session.snapshot().status).toBe("ended");
    });

    it("refuses a request and says why, on the screen too, while the call stays up", async () => {
        const source = callSample();
        const callId = await opened(source, "sample-launch-1440");
        const session = new CallSession({ source, podId: "sample-launch-1440", post: () => undefined, os: "android" });
        const notices: string[] = [];
        session.subscribe(() => { const notice = session.snapshot().notice; if (notice && notices.at(-1) !== notice) notices.push(notice); });
        await session.start(callId, START);
        await vi.advanceTimersByTimeAsync(20000);
        expect(notices).toEqual([expect.stringContaining("usage limit")]);
        expect(session.snapshot()).toMatchObject({ status: "live", working: false, canvas: [] });
        session.end();
    });

    it("holds a call in the chat it was started from, and picks a call up again where it was", async () => {
        const source = callSample();
        expect(await opened(source, "sample-books-986", "chat-7")).toBe("chat-7");
        const callId = await opened(source, "sample-books-986");
        const first = new CallSession({ source, podId: "sample-books-986", post: () => undefined, os: "android" });
        await first.start(callId, START);
        await vi.advanceTimersByTimeAsync(20000);
        expect(first.snapshot().canvas.length).toBe(1);
        first.end();

        /* Calling again: the same call, its canvas replayed, the voice back. */
        expect(await opened(source, "sample-books-986", callId)).toBe(callId);
        const again = new CallSession({ source, podId: "sample-books-986", post: () => undefined, os: "android" });
        await again.start(callId, START);
        await vi.advanceTimersByTimeAsync(5000);
        expect(again.snapshot().canvas.map((one) => one.id)).toEqual(first.snapshot().canvas.map((one) => one.id));
        expect(again.snapshot().transcript.map((line) => line.text)).toEqual(["I’m back. The report is still on your screen."]);
        again.end();
    });

    it("stops every timer when it is closed", async () => {
        const frames: CallFrame[] = [];
        let closes = 0;
        const link = scriptedCall("sample-launch-1440", "c1", { onFrame: (frame) => frames.push(frame), onAudio: () => undefined, onClose: () => { closes += 1; } });
        await vi.advanceTimersByTimeAsync(800);
        expect(frames.map((one) => one.type)).toEqual(["ready"]);
        link.close();
        link.close();
        await vi.advanceTimersByTimeAsync(20000);
        expect(frames.length).toBe(1);
        expect(closes).toBe(1);
    });
});
