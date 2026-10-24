/* A call's sound: the microphone, the speaker and the voice server's socket,
   run by the web's own voice clients (`lemma-frontend/src/call`), unchanged.

   The app loads this page with the web server as its base URL, so a client's
   `new URL("/api/live", location.href)` is that server's gateway. Only words
   and switches cross to the app (`FromPage`, `ToPage` in the call part's
   model): transcripts, how loud the voice is, whether it is still playing.
   Audio stays here. The page holds no session and reaches nothing but the
   gateway. */

import { GeminiLiveClient, MIC_SAMPLE_RATE as GEMINI_MIC_RATE } from "@/call/gemini-live-client";
import { GptLiveClient, MIC_SAMPLE_RATE as GPT_LIVE_MIC_RATE } from "@/call/gpt-live-client";
import { createPCMPlayer, startMicPCMStream, type MicStream, type PCMPlayer } from "@/call/pcm-audio";
import type { CallTransport } from "@/call/transport";
import type { FromPage, ToPage, VoiceProvider } from "../../../src/areas/more/call/model";

declare global {
    interface Window {
        ReactNativeWebView?: { postMessage: (data: string) => void };
        __fromApp?: (message: ToPage) => void;
    }
}

function post(message: FromPage): void {
    window.ReactNativeWebView?.postMessage(JSON.stringify(message));
}

/** How often the voice's loudness and playing are looked at. */
const METER_MS = 80;
/** At most this often, "a piece of the voice arrived". */
const SPOKE_MS = 250;

interface Call {
    attempt: number;
    client: CallTransport;
    player: PCMPlayer;
    micRate: number;
    mic: MicStream | null;
    muted: boolean;
    meter: ReturnType<typeof setInterval>;
    level: number;
    playing: boolean;
    spokeAt: number;
    spokeTimer: ReturnType<typeof setTimeout> | null;
}

let call: Call | null = null;
let attempts = 0;

function messageOf(problem: unknown, fallback: string): string {
    return problem instanceof Error && problem.message ? problem.message : fallback;
}

function stop(): void {
    const held = call;
    call = null;
    attempts += 1;
    if (!held) return;
    clearInterval(held.meter);
    if (held.spokeTimer) clearTimeout(held.spokeTimer);
    held.mic?.stop();
    held.player.stop();
    held.client.disconnect();
}

function fail(attempt: number, message: string, cause?: "microphone" | "insecure" | "voice"): void {
    if (!call || call.attempt !== attempt) return;
    stop();
    post({ type: "error", message, ...(cause ? { cause } : {}) });
}

/* The drain in the app waits on the voice's last sound; it needs the time of
   it, not every chunk. The first of a run goes at once and the last always
   goes, so the time it hears is never more than a moment behind. */
function spoke(held: Call): void {
    const now = Date.now();
    if (now - held.spokeAt >= SPOKE_MS) {
        held.spokeAt = now;
        post({ type: "spoke" });
        return;
    }
    if (held.spokeTimer) return;
    held.spokeTimer = setTimeout(() => {
        held.spokeTimer = null;
        if (call !== held) return;
        held.spokeAt = Date.now();
        post({ type: "spoke" });
    }, SPOKE_MS - (now - held.spokeAt));
}

function playing(held: Call, on: boolean): void {
    if (held.playing === on) return;
    held.playing = on;
    post({ type: "playing", on });
}

function start(provider: VoiceProvider, instructions: string): void {
    if (call) return;
    const attempt = ++attempts;
    /* Without a secure context there is no `mediaDevices` at all, and the
       call would connect only to fail at the microphone. */
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        post({ type: "error", message: "This page is not a secure context.", cause: "insecure" });
        return;
    }
    const Client = provider === "gpt-live" ? GptLiveClient : GeminiLiveClient;
    const player = createPCMPlayer();
    let held: Call | null = null;
    const client = new Client({
        systemInstruction: instructions,
        onAudioChunk: (chunk) => {
            if (!held || call !== held) return;
            player.push(chunk);
            playing(held, true);
            spoke(held);
        },
        /* Gemini's barge-in: what was queued is dropped here, where it plays. */
        onInterrupted: () => {
            if (!held || call !== held) return;
            player.clear();
            playing(held, false);
            post({ type: "interrupted" });
        },
        onTurnComplete: () => { if (held && call === held) post({ type: "turn-complete" }); },
        onThinking: (on) => { if (held && call === held) post({ type: "thinking", on }); },
        onTranscript: (chunk) => {
            if (held && call === held) post({ type: "transcript", speaker: chunk.speaker, text: chunk.text ?? "", final: Boolean(chunk.final) });
        },
        onOpen: () => { if (held && call === held) post({ type: "open" }); },
        onClose: (reason) => {
            if (!held || call !== held) return;
            stop();
            post({ type: "closed", reason });
        },
        onError: (problem) => fail(attempt, messageOf(problem, "The call dropped."), "voice"),
    });
    held = {
        attempt,
        client,
        player,
        micRate: provider === "gpt-live" ? GPT_LIVE_MIC_RATE : GEMINI_MIC_RATE,
        mic: null,
        muted: false,
        level: 0,
        playing: false,
        spokeAt: 0,
        spokeTimer: null,
        meter: setInterval(() => {
            if (!held || call !== held) return;
            /* Rounded, so a level that has not moved is not said again. */
            const level = Math.round(player.getLevel() * 20) / 20;
            if (level !== held.level) {
                held.level = level;
                post({ type: "level", value: level });
            }
            if (held.playing && !player.isPlaying()) playing(held, false);
        }, METER_MS),
    };
    call = held;
    client.connect().catch((problem) => fail(attempt, messageOf(problem, "Could not connect to the voice server."), "voice"));
}

async function listen(): Promise<void> {
    const held = call;
    if (!held || held.mic) return;
    try {
        const mic = await startMicPCMStream((chunk) => {
            if (call === held && !held.muted) held.client.sendAudioChunk(chunk);
        }, held.micRate);
        if (call !== held) {
            mic.stop();
            return;
        }
        held.mic = mic;
        post({ type: "listening" });
    } catch (problem) {
        /* Refused (by the person or the phone), or no microphone at all. */
        const name = problem instanceof Error ? problem.name : "";
        const refused = name === "NotAllowedError" || name === "SecurityError" || name === "NotFoundError";
        fail(held.attempt, messageOf(problem, "The microphone could not be opened."), refused ? "microphone" : "voice");
    }
}

window.__fromApp = (message: ToPage) => {
    switch (message.type) {
        case "start":
            start(message.provider, message.instructions);
            return;
        case "history":
            call?.client.sendHistory(message.text);
            return;
        case "listen":
            void listen();
            return;
        case "reply":
            call?.client.sendAgentReply(message.text, message.id);
            return;
        case "widget":
            call?.client.sendAgentWidget(message.label);
            return;
        case "mute":
            if (call) call.muted = message.on;
            return;
        case "clear":
            if (call) {
                call.player.clear();
                playing(call, false);
            }
            return;
        case "end":
            stop();
            return;
    }
};

post({ type: "ready" });
