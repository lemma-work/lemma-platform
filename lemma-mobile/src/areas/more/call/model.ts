import type { CallLine } from "@web/call/call-transcript";
import { statusLine } from "@web/call/call-status";

/** A voice call with a Lemma (PARITY.md, G), as the phone reads it.
 *
 *  The call is held by the server: one socket (the web's `call-socket.ts`)
 *  carries the microphone up and the voice down as 16-bit PCM, and what is
 *  said, what is being worked on and what goes on the canvas as JSON. The
 *  socket is React Native's, since it carries the person's token. The sound
 *  is a page in a WebView (`lemma-mobile/webview/src/call`) running the web's
 *  own Web Audio capture and playback (`pcm-audio.ts`): PCM crosses the
 *  bridge as base64 in the small messages below, and nothing else does. The
 *  page imports these types, so both halves are written against one
 *  protocol. */

/** What React Native tells the page. */
export type ToPage =
    /** Open the microphone and send what it hears. */
    | { type: "listen" }
    /** The voice is ready: play what comes at this rate. */
    | { type: "voice"; rate: number }
    /** Some of the voice, base64 16-bit mono PCM, to play after what is queued. */
    | { type: "audio"; pcm: string }
    /** The caller cut in: drop what the voice has queued. */
    | { type: "clear" }
    /** Stop sending the microphone while on. */
    | { type: "mute"; on: boolean }
    | { type: "end" };

/** Why the page could not open the microphone. */
export type PageCause = "microphone" | "insecure";

/** What the page tells React Native. */
export type FromPage =
    /** The page has loaded and can take `listen`. */
    | { type: "ready" }
    /** The microphone is open. */
    | { type: "listening" }
    /** Some of the microphone, base64 16-bit mono PCM at 16 kHz. */
    | { type: "mic"; pcm: string }
    /** How loud the voice is, 0 to 1, for the ring. */
    | { type: "level"; value: number }
    | { type: "error"; message: string; cause?: PageCause };

const CAUSES = new Set<string>(["microphone", "insecure"]);

/** A message from the page, checked. Anything else is dropped: the page is
 *  ours, but a WebView is a place where anything might post. */
export function parseFromPage(data: string): FromPage | null {
    let said: unknown;
    try {
        said = JSON.parse(data);
    } catch {
        return null;
    }
    if (!said || typeof said !== "object") return null;
    const m = said as Record<string, unknown>;
    switch (m.type) {
        case "ready":
        case "listening":
            return { type: m.type };
        case "mic":
            return typeof m.pcm === "string" && m.pcm.length > 0 ? { type: "mic", pcm: m.pcm } : null;
        case "level": {
            const value = typeof m.value === "number" && Number.isFinite(m.value) ? Math.max(0, Math.min(1, m.value)) : null;
            return value === null ? null : { type: "level", value };
        }
        case "error":
            return {
                type: "error",
                message: typeof m.message === "string" ? m.message : "",
                ...(typeof m.cause === "string" && CAUSES.has(m.cause) ? { cause: m.cause as PageCause } : {}),
            };
        default:
            return null;
    }
}

/** A message for the page, as the JavaScript that hands it over. */
export function scriptFor(message: ToPage): string {
    return `window.__fromApp && window.__fromApp(${JSON.stringify(message)});true;`;
}

/* ── PCM over the bridge ─────────────────────────────────────────────── */

/** Bytes as base64, in pieces so a long buffer never overflows the stack. */
export function toBase64(bytes: Uint8Array): string {
    let text = "";
    for (let at = 0; at < bytes.length; at += 0x8000) text += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
    return btoa(text);
}

export function fromBase64(text: string): Uint8Array {
    const raw = atob(text);
    const bytes = new Uint8Array(raw.length);
    for (let at = 0; at < raw.length; at += 1) bytes[at] = raw.charCodeAt(at);
    return bytes;
}

/** The microphone's samples, from the page's base64. An odd last byte is
 *  not half a sample to send. */
export function samplesOf(pcm: string): Int16Array {
    const bytes = fromBase64(pcm);
    return new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
}

/** The voice's chunks as they came, joined for one trip to the page: a trip
 *  per chunk is many a second, and each is a script the page has to run. */
export function joined(chunks: readonly ArrayBuffer[]): Uint8Array {
    const total = chunks.reduce((sum, one) => sum + one.byteLength, 0);
    const out = new Uint8Array(total);
    let at = 0;
    for (const one of chunks) {
        out.set(new Uint8Array(one), at);
        at += one.byteLength;
    }
    return out;
}

/* ── what the screen says ────────────────────────────────────────────── */

/** Where a call is, as the screen draws it. */
export type CallPhase =
    /** Asking the server whether it can carry a call. */
    | { kind: "checking" }
    | { kind: "unconfigured" }
    /** Asking the phone for the microphone. */
    | { kind: "asking" }
    /** The microphone was refused; `final` when only the phone's settings can
     *  turn it back on. */
    | { kind: "refused"; final: boolean }
    | { kind: "connecting" }
    | { kind: "live" }
    | { kind: "ended" }
    | { kind: "failed"; error: string };

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "error";

/** What the hook is doing before the call itself starts. */
export type Setup =
    | { kind: "checking" }
    | { kind: "unconfigured" }
    | { kind: "asking" }
    | { kind: "refused"; final: boolean }
    | { kind: "opening" }
    | { kind: "failed"; error: string }
    /** Ended before the call itself began. */
    | { kind: "cancelled" }
    | { kind: "started" };

/** One phase from the setup and the call's own status. A microphone the
 *  page was refused (iOS asks when the page first wants it) is said the way
 *  a refusal before the call is. */
export function phaseOf(setup: Setup, status: CallStatus, error: string | null, cause: PageCause | null = null): CallPhase {
    switch (setup.kind) {
        case "checking":
        case "unconfigured":
        case "asking":
        case "refused":
        case "failed":
            return setup;
        case "opening":
            return { kind: "connecting" };
        case "cancelled":
            return { kind: "ended" };
        case "started":
            if (status === "live") return { kind: "live" };
            if (status === "error" && cause === "microphone") return { kind: "refused", final: true };
            if (status === "error") return { kind: "failed", error: error ?? "The call stopped." };
            if (status === "ended") return { kind: "ended" };
            return { kind: "connecting" };
    }
}

export const NOT_SET_UP = "Voice calls aren’t set up on this server.";

/** Whether the server can place a call: `calls.config()` names at least one
 *  voice engine. Anything else is no: a call that cannot be checked is not
 *  one to offer (the web's `voiceConfigured`). */
export function configuredFrom(config: unknown): boolean {
    const engines = config && typeof config === "object" ? (config as { engines?: unknown }).engines : null;
    return Array.isArray(engines) && engines.length > 0;
}

/** The line under the name while a call is on: the web's `statusLine`, which
 *  decides what to say, said without its dashes. */
export function callLine(input: Parameters<typeof statusLine>[0]): string {
    const said = statusLine(input);
    if (!/\s—\s/.test(said)) return said;
    const joinedLine = said.replace(/\s+—\s+(.)/g, (_, next: string) => ". " + next.toUpperCase());
    return /[.?!…]$/.test(joinedLine) ? joinedLine : joinedLine + ".";
}

/** The words at the top of the screen in each phase. */
export function callWords(phase: CallPhase, name: string, live: { muted?: boolean; thinking?: boolean } = {}, os: "ios" | "android" = "android"): { title: string; line: string; tone: "quiet" | "bad" } {
    switch (phase.kind) {
        case "checking":
            return { title: `Connecting to ${name}…`, line: "", tone: "quiet" };
        case "unconfigured":
            return { title: name, line: NOT_SET_UP, tone: "quiet" };
        case "asking":
            return { title: `Connecting to ${name}…`, line: `So ${name} can hear you, the app needs the microphone.`, tone: "quiet" };
        case "refused":
            return { title: `${name} can’t hear you`, line: microphoneOff(os, phase.final), tone: "bad" };
        case "connecting":
            /* Nothing is listening yet, so only muted is worth a line. */
            return { title: `Connecting to ${name}…`, line: live.muted ? callLine({ muted: true, teammate: name }) : "", tone: "quiet" };
        case "live":
            return { title: `On a call with ${name}`, line: callLine({ ...live, teammate: name }), tone: "quiet" };
        case "ended":
            return { title: "Call ended", line: "", tone: "quiet" };
        case "failed":
            return { title: "The call stopped", line: phase.error, tone: "bad" };
    }
}

/** Where the microphone is turned back on, on this phone. */
export function microphoneOff(os: "ios" | "android", final = true): string {
    const where = os === "ios" ? "Settings, Lemma, Microphone" : "Settings, Apps, Lemma, Permissions, Microphone";
    return final
        ? `The app doesn’t have the microphone. Turn it on in ${where}, then call again.`
        : `The app needs the microphone to hear you. Allow it when the phone asks, or turn it on in ${where}.`;
}

/** What a failure the page reports says to a person. */
export function pageProblem(message: string, cause: PageCause | undefined, os: "ios" | "android"): string {
    if (cause === "microphone") return microphoneOff(os);
    if (cause === "insecure") return "This server isn’t on a secure (https) address, so the phone won’t open the microphone for it.";
    return message.trim() || "The call dropped.";
}

/** The one caption: the line being said, by whom, its end kept when it runs
 *  long (the end is what is being said now). */
export function captionOf(lines: readonly CallLine[], name: string, limit = 160): { who: string; text: string } | null {
    const line = lines[lines.length - 1];
    if (!line || !line.text.trim()) return null;
    const text = line.text.trim();
    return { who: line.speaker === "them" ? "You" : name, text: text.length > limit ? "…" + text.slice(-(limit - 1)).trimStart() : text };
}

/** After the call: the call is a chat of its own, where what was asked and
 *  done is kept. */
export function afterWords(chatId: string | null): { line: string; link: string | null } {
    if (!chatId) return { line: "Nothing from this call went to a chat.", link: null };
    return { line: "What was asked and done is in the call’s chat.", link: "Open the chat" };
}

/** "2 of 3": where the screen is among what was shown. */
export function deckWhere(at: number, count: number): string {
    return `${at + 1} of ${count}`;
}

/** What the screen shows: the newest the moment it arrives; paging back is
 *  for looking something up, not for holding the screen. */
export function shownIndex(showing: number, count: number): number {
    return Math.max(0, Math.min(showing, count - 1));
}
