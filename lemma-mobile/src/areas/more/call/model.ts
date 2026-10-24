import type { Speaker } from "@web/call/live-turns";
import type { CallLine } from "@web/call/call-transcript";
import { statusLine } from "@web/call/call-status";
import type { PlanStepState } from "@web/thread/turns";

/** A voice call with a Lemma (PARITY.md, G), as the phone reads it.
 *
 *  The call is two halves. Its sound is a page in a WebView
 *  (`lemma-mobile/webview/src/call`), running the web's own voice clients
 *  against the web server's voice gateway: it holds the microphone, the
 *  speaker and that socket. Everything else, routing what is said to the
 *  Lemma's chats with the phone's own session, runs here. The two talk in
 *  the small JSON messages below: words, levels and states, never audio, and
 *  never a token. The page imports these types, so both halves are written
 *  against one protocol. */

/** Which voice model holds the microphone: the deployment's `voiceProvider`. */
export type VoiceProvider = "gemini" | "gpt-live";

/** What React Native tells the page. */
export type ToPage =
    /** Build the voice session and connect to the gateway. */
    | { type: "start"; provider: VoiceProvider; instructions: string }
    /** Quiet context: the written conversation, a snapshot, routine progress. */
    | { type: "history"; text: string }
    /** Open the microphone. Sent once the history is in. */
    | { type: "listen" }
    /** The update the voice should say, at a pause. */
    | { type: "reply"; text: string; id?: string }
    /** Something is on the call's screen now, named. */
    | { type: "widget"; label: string }
    | { type: "mute"; on: boolean }
    /** Stop what the voice is saying now: a clarification takes the floor. */
    | { type: "clear" }
    | { type: "end" };

/** Why the page could not go on. */
export type PageCause = "microphone" | "insecure" | "voice";

/** What the page tells React Native. */
export type FromPage =
    /** The page has loaded and can take `start`. */
    | { type: "ready" }
    /** The voice session is up. */
    | { type: "open" }
    /** The microphone is open. */
    | { type: "listening" }
    | { type: "transcript"; speaker: Speaker; text: string; final: boolean }
    /** How loud the voice is, 0 to 1, for the ring. */
    | { type: "level"; value: number }
    /** A piece of the voice's audio arrived (at most a few a second). */
    | { type: "spoke" }
    /** Whether the voice is still playing what it was sent. */
    | { type: "playing"; on: boolean }
    /** Reasoning is still going after the voice stopped (Gemini only). */
    | { type: "thinking"; on: boolean }
    | { type: "interrupted" }
    | { type: "turn-complete" }
    | { type: "closed"; reason: string }
    | { type: "error"; message: string; cause?: PageCause };

const SPEAKERS = new Set<string>(["them", "us"]);
const CAUSES = new Set<string>(["microphone", "insecure", "voice"]);

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
    const flag = (value: unknown) => value === true;
    switch (m.type) {
        case "ready":
        case "open":
        case "listening":
        case "spoke":
        case "interrupted":
        case "turn-complete":
            return { type: m.type };
        case "transcript":
            if (typeof m.speaker !== "string" || !SPEAKERS.has(m.speaker) || typeof m.text !== "string") return null;
            return { type: "transcript", speaker: m.speaker as Speaker, text: m.text, final: flag(m.final) };
        case "level": {
            const value = typeof m.value === "number" && Number.isFinite(m.value) ? Math.max(0, Math.min(1, m.value)) : null;
            return value === null ? null : { type: "level", value };
        }
        case "playing":
        case "thinking":
            return { type: m.type, on: flag(m.on) };
        case "closed":
            return { type: "closed", reason: typeof m.reason === "string" ? m.reason : "" };
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

/* ── before a call ───────────────────────────────────────────────────── */

/** `GET {web}/api/call/config` → `{configured}`. Only a plain `true` is yes:
 *  a call that cannot be checked is not one to place. */
export function configuredFrom(body: unknown): boolean {
    return Boolean(body && typeof body === "object" && (body as { configured?: unknown }).configured === true);
}

/** `voiceProvider` from `{web}/site-config.js` (`window.__LEMMA_SITE__={…};`).
 *  Gemini unless it says gpt-live, as the web decides (`use-call.ts`). */
export function voiceProviderOf(script: string | null | undefined): VoiceProvider {
    const match = /__LEMMA_SITE__\s*=\s*(\{[\s\S]*\})\s*;?\s*$/.exec((script ?? "").trim());
    if (!match) return "gemini";
    try {
        const config = JSON.parse(match[1]) as { voiceProvider?: unknown };
        return config.voiceProvider === "gpt-live" ? "gpt-live" : "gemini";
    } catch {
        return "gemini";
    }
}

export interface CallReadiness {
    configured: boolean;
    provider: VoiceProvider;
}

/* ── the drain: when the voice may speak ─────────────────────────────── */

/** The web's timings (`use-call.ts`), named. */
export const DRAIN = {
    /** How soon a queued update is looked at again. */
    RETRY_MS: 350,
    /** A clarification waits only for the caller's own floor... */
    FLOOR_MS: 500,
    /** ...and looks again this soon. */
    FLOOR_RETRY_MS: 150,
    /** The voice counts as busy this long after its last sound or thought. */
    BUSY_MS: 8000,
    /** A reply sent is waited on this long for its first sound. */
    AWAIT_MS: 15000,
    /** The quiet after either side last spoke before an update may be said. */
    QUIET_MS: 1200,
} as const;

/** What the drain knows when it looks. Times are `Date.now()`; 0 is never. */
export interface DrainClock {
    now: number;
    /** The caller's last words. */
    lastUser: number;
    /** The voice's last sound. */
    lastVoice: number;
    /** Since when the voice has been busy (speaking or thinking); 0 when not. */
    providerBusy: number;
    /** When a reply was sent and is still waiting for sound; 0 when not. */
    awaitingAudio: number;
    /** Whether the voice is still playing. */
    playing: boolean;
}

export type DrainStep = { kind: "wait"; ms: number } | { kind: "go"; clear: boolean };

/** Whether a queued update may go to the voice now: the drain from the web's
 *  `use-call.ts`, out of its closure.
 *
 *  A clarification is the answer to what the caller just said, so it waits
 *  only for the caller to finish and then takes the floor, cutting off
 *  whatever the voice was saying (`clear`). Anything else waits for a real
 *  pause: the voice not busy, nothing still awaited or playing, and a moment
 *  of quiet from both sides. */
export function drainStep(at: DrainClock, clarifying: boolean): DrainStep {
    if (clarifying) {
        if (at.now - at.lastUser < DRAIN.FLOOR_MS) return { kind: "wait", ms: DRAIN.FLOOR_RETRY_MS };
        return { kind: "go", clear: true };
    }
    if (
        (at.providerBusy && at.now - at.providerBusy < DRAIN.BUSY_MS)
        || (at.awaitingAudio && at.now - at.awaitingAudio < DRAIN.AWAIT_MS)
        || at.playing
        || at.now - Math.max(at.lastUser, at.lastVoice) < DRAIN.QUIET_MS
    ) {
        return { kind: "wait", ms: DRAIN.RETRY_MS };
    }
    return { kind: "go", clear: false };
}

/** The spoken exchange as the router reads it ("User: …" / "Voice: …"), the
 *  web's fold of transcript fragments (`use-call.ts`), kept to its end. */
export interface Spoken {
    history: string;
    lastSpeaker: Speaker | null;
}

export const SPOKEN_LIMIT = 90000;

export function spokenAfter(spoken: Spoken, chunk: { speaker: Speaker; text: string; final?: boolean }): Spoken {
    let { history, lastSpeaker } = spoken;
    if (chunk.text) {
        const prefix = lastSpeaker === chunk.speaker ? "" : `\n${chunk.speaker === "them" ? "User" : "Voice"}: `;
        history = (history + prefix + chunk.text).slice(-SPOKEN_LIMIT);
        lastSpeaker = chunk.speaker;
    }
    if (chunk.final) lastSpeaker = null;
    return { history, lastSpeaker };
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

/** The line under the name while a call is on: the web's `statusLine`, which
 *  decides what to say, said without its dashes. */
export function callLine(input: Parameters<typeof statusLine>[0]): string {
    const said = statusLine(input);
    if (!/\s—\s/.test(said)) return said;
    const joined = said.replace(/\s+—\s+(.)/g, (_, next: string) => ". " + next.toUpperCase());
    return /[.?!…]$/.test(joined) ? joined : joined + ".";
}

/** The words at the top of the screen in each phase. */
export function callWords(phase: CallPhase, name: string, live: { muted?: boolean; plan?: PlanStepState[]; thinking?: boolean } = {}, os: "ios" | "android" = "android"): { title: string; line: string; tone: "quiet" | "bad" } {
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

/** After the call: where what was said and done went. */
export function afterWords(chat: { id: string; title: string | null } | null): { line: string; link: string | null } {
    if (!chat) return { line: "Nothing from this call went to a chat.", link: null };
    const title = chat.title?.trim();
    return { line: title ? `What was asked and done is in “${title}”.` : "What was asked and done is in the call’s chat.", link: "Open the chat" };
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
