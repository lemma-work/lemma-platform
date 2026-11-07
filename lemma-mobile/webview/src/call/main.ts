/* A call's sound: the microphone and the speaker, in Web Audio, with the
   web's own capture and playback (`lemma-frontend/src/call/pcm-audio.ts`).

   The call itself is the app's: its socket to the server lives in React
   Native, which holds the session. Only 16-bit PCM crosses, as base64, with
   a few words of state (`ToPage`, `FromPage` in the call part's model). The
   page holds no session and reaches nothing. */

import { createPCMPlayer, startMicPCMStream, type MicStream, type PCMPlayer } from "@/call/pcm-audio";
import { fromBase64, toBase64, type FromPage, type ToPage } from "../../../src/areas/more/call/model";

declare global {
    interface Window {
        ReactNativeWebView?: { postMessage: (data: string) => void };
        __fromApp?: (message: ToPage) => void;
    }
}

/** How often the voice's loudness is looked at, for the ring. */
const METER_MS = 100;

let mic: MicStream | null = null;
let player: PCMPlayer | null = null;
let muted = false;
let meter: ReturnType<typeof setInterval> | null = null;
let lastLevel = -1;
/** Bumped on every stop, so a microphone that opens after the call ended is shut. */
let opening = 0;

function post(message: FromPage): void {
    window.ReactNativeWebView?.postMessage(JSON.stringify(message));
}

async function listen(): Promise<void> {
    if (mic) return;
    const attempt = ++opening;
    /* A page outside a secure context never gets a microphone: say so before
       the call goes on with nobody heard. */
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        post({ type: "error", message: "This page is not a secure context.", cause: "insecure" });
        return;
    }
    try {
        const opened = await startMicPCMStream((pcm) => {
            if (!muted) post({ type: "mic", pcm: toBase64(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength)) });
        });
        if (attempt !== opening) {
            opened.stop();
            return;
        }
        mic = opened;
        post({ type: "listening" });
    } catch (problem) {
        if (attempt !== opening) return;
        /* Refused (by the person or the phone), or no microphone at all. */
        const name = problem instanceof Error ? problem.name : "";
        const refused = name === "NotAllowedError" || name === "SecurityError" || name === "NotFoundError";
        const message = problem instanceof Error && problem.message ? problem.message : "The microphone could not be opened.";
        post({ type: "error", message, ...(refused ? { cause: "microphone" as const } : {}) });
    }
}

function measure(): void {
    if (meter) return;
    meter = setInterval(() => {
        const level = player?.isPlaying() ? Math.round(player.getLevel() * 20) / 20 : 0;
        if (level === lastLevel) return;
        lastLevel = level;
        post({ type: "level", value: level });
    }, METER_MS);
}

function stop(): void {
    opening += 1;
    mic?.stop();
    mic = null;
    player?.stop();
    player = null;
    if (meter) clearInterval(meter);
    meter = null;
    muted = false;
    lastLevel = -1;
}

window.__fromApp = (message: ToPage) => {
    switch (message.type) {
        case "listen":
            void listen();
            break;
        case "voice":
            player?.stop();
            player = createPCMPlayer(message.rate);
            measure();
            break;
        case "audio": {
            const bytes = fromBase64(message.pcm);
            player?.push(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
            break;
        }
        case "clear":
            player?.clear();
            break;
        case "mute":
            muted = message.on;
            break;
        case "end":
            stop();
            break;
    }
};

post({ type: "ready" });
