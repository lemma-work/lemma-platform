import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CallFrame, CallSocketHandlers } from "@web/call/call-socket";
import { podBrief } from "@web/call/pod-brief";
import * as callPage from "../src/areas/more/call/call-page.generated";
import { AUDIO_FLUSH_MS, CallSession } from "../src/areas/more/call/machine";
import {
    afterWords,
    callLine,
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
    samplesOf,
    scriptFor,
    shownIndex,
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

    it("points at the call's own chat after it", () => {
        expect(afterWords("call-1")).toEqual({ line: "What was asked and done is in the call’s chat.", link: "Open the chat" });
        expect(afterWords(null)).toEqual({ line: "Nothing from this call went to a chat.", link: null });
        expect(deckWhere(2, 3)).toBe("3 of 3");
        expect(shownIndex(5, 3)).toBe(2);
    });

    it("never says pod, teammate, agent or an exclamation", () => {
        const phases = [{ kind: "checking" }, { kind: "unconfigured" }, { kind: "asking" }, { kind: "refused", final: true }, { kind: "refused", final: false }, { kind: "connecting" }, { kind: "live" }, { kind: "ended" }] as const;
        const said = [
            ...phases.flatMap((phase) => Object.values(callWords(phase, "Support", { muted: phase.kind === "live", thinking: true }))),
            afterWords(null).line, afterWords("x").line, pageProblem("", "insecure", "ios"), microphoneOff("android", false),
        ].join(" ");
        expect(said).not.toMatch(/\b(pod|teammate|agent|bot|assistant)s?\b|!|—/i);
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

function harness(options: { sound?: boolean; failConnect?: boolean } = {}) {
    const posted: ToPage[] = [];
    const sent: Record<string, unknown>[] = [];
    const audio: Int16Array[] = [];
    let handlers: CallSocketHandlers | null = null;
    let closed = 0;
    const link: CallLink = {
        sendAudio: (pcm) => { audio.push(pcm); },
        send: (control) => { sent.push(control); },
        close: () => { closed += 1; handlers?.onClose(null); },
    };
    const source: CallSource = {
        sound: options.sound ?? true,
        callable: async () => true,
        open: async () => "call-1",
        brief: async () => "",
        connect: async (_podId, _callId, _start, heard) => {
            if (options.failConnect) throw new Error("The call could not connect.");
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
        closed: () => closed,
        frame: (frame: CallFrame) => handlers?.onFrame(frame),
        voice: (bytes: number[]) => handlers?.onAudio(new Uint8Array(bytes).buffer),
        drop: (reason: string | null) => handlers?.onClose(reason),
        types: () => posted.map((one) => one.type),
    };
}

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
        call.frame({ type: "transcript", speaker: "voice", text: " there.", final: true });
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
        call.session.looking(canvas[0]);
        expect(call.sent).toContainEqual({ type: "canvas.focus", tool_call_id: "t1" });
    });

    it("says why it stopped, and ends plainly on an ordinary close", async () => {
        const dropped = harness();
        await dropped.session.start("call-1", START);
        dropped.drop("The voice is not available right now.");
        expect(dropped.session.snapshot()).toMatchObject({ status: "error", error: "The voice is not available right now." });
        expect(dropped.types()).toContain("end");

        const ended = harness();
        await ended.session.start("call-1", START);
        ended.drop(null);
        expect(ended.session.snapshot().status).toBe("ended");

        const refused = harness({ failConnect: true });
        await refused.session.start("call-1", START);
        expect(refused.session.snapshot()).toMatchObject({ status: "error", error: "The call could not connect." });
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

    it("connects, talks both ways, works, shows one thing and answers, with no server", async () => {
        const source = callSample();
        expect(source.sound).toBe(false);
        expect(await source.callable("sample-support-1140")).toBe(true);
        const opening = source.open("sample-support-1140", "Support");
        await vi.advanceTimersByTimeAsync(300);
        const callId = await opening;
        const session = new CallSession({ source, podId: "sample-support-1140", post: () => undefined, os: "android" });
        await session.start(callId, START);
        await vi.advanceTimersByTimeAsync(800);
        expect(session.snapshot().status).toBe("live");
        await vi.advanceTimersByTimeAsync(20000);
        const view = session.snapshot();
        expect(view.transcript.map((line) => line.speaker)).toEqual(["us", "them", "us", "us"]);
        expect(view.transcript[1].text).toBe("How many tickets are still waiting on us?");
        expect(view.canvas.map((one) => one.resource)).toEqual([expect.objectContaining({ type: "TABLE", name: "tickets" })]);
        expect(view.working).toBe(false);
        session.end();
        expect(session.snapshot().status).toBe("ended");
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
