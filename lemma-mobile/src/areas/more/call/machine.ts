import { drainStep, pageProblem, spokenAfter, type CallStatus, type FromPage, type PageCause, type Spoken, type ToPage, type VoiceProvider } from "./model";
import type { EngineLink } from "./source";
import { foldTranscript, type CallLine } from "@web/call/call-transcript";
import { newId } from "@web/call/ids";
import type { VoiceEvent } from "@web/call/routing";
import { UtteranceBuffer } from "@web/call/utterance-buffer";
import { VoiceEvents } from "@web/call/voice-events";
import { instructionFor } from "@web/call/voice-instructions";

/** The web's `use-call.ts` for a phone: the call itself, with its sound in
 *  an engine (the page in a WebView, or a scripted voice) and everything
 *  that decides in here.
 *
 *  Out of React so it can be read and tested as what it is: a small state
 *  machine on a clock. What the engine says arrives at `hear`; what is said
 *  becomes utterances routed through `handlers`; what comes back is queued
 *  and handed to the voice at a pause (`drainStep`). Each start is an
 *  attempt, and anything arriving for an older one is dropped, as on the web. */

export interface CallHandlers {
    observeTranscript: (text: string) => void;
    route: (id: string, text: string, transcript: string) => Promise<void>;
    subscribe: (listener: (event: VoiceEvent) => void) => () => void;
    history?: () => string | Promise<string>;
    context?: () => string | Promise<string>;
}

export interface CallView {
    status: CallStatus;
    error: string | null;
    muted: boolean;
    /** Reasoning still running behind a finished turn (Gemini only). */
    thinking: boolean;
    transcript: CallLine[];
    /** What stopped the call, when the page said. */
    cause: PageCause | null;
}

export interface Clock {
    now(): number;
    setTimeout(run: () => void, ms: number): unknown;
    clearTimeout(timer: unknown): void;
}

const SYSTEM_CLOCK: Clock = {
    now: () => Date.now(),
    setTimeout: (run, ms) => setTimeout(run, ms),
    clearTimeout: (timer) => clearTimeout(timer as ReturnType<typeof setTimeout>),
};

/** How long the page may take to load before the call gives up on it. */
export const LOAD_MS = 15_000;

const IDLE: CallView = { status: "idle", error: null, muted: false, thinking: false, transcript: [], cause: null };

export class CallMachine {
    private view: CallView = IDLE;
    private readonly listeners = new Set<() => void>();
    private attempt = 0;
    private utterances: UtteranceBuffer | null = null;
    private unsubscribe: (() => void) | null = null;
    private eventTimer: unknown = null;
    private loadTimer: unknown = null;
    private lastUser = 0;
    private lastVoice = 0;
    private awaitingAudio = 0;
    private providerBusy = 0;
    private playing = false;
    private latestRequest: string | null = null;
    private spoken: Spoken = { history: "", lastSpeaker: null };
    private events = new VoiceEvents();
    private quietUpdates = new Map<string, string>();
    /** The page has loaded, and the `start` it is to be sent. */
    private engineReady = false;
    private pendingStart: ToPage | null = null;
    private mutedNow = false;
    private readonly clock: Clock;

    constructor(private readonly options: {
        /** Read at each use, so a re-render's handlers are the ones called. */
        handlers: () => CallHandlers;
        engine: EngineLink;
        os?: "ios" | "android";
        clock?: Clock;
    }) {
        this.clock = options.clock ?? SYSTEM_CLOCK;
    }

    subscribe = (listener: () => void) => {
        this.listeners.add(listener);
        return () => { this.listeners.delete(listener); };
    };

    snapshot = () => this.view;

    /* How loud the voice is, 0 to 1, for the ring: a store of its own, since
       it moves a dozen times a second and nothing else on screen need redraw
       when it does. */
    private level = 0;
    private readonly levelListeners = new Set<() => void>();

    subscribeLevel = (listener: () => void) => {
        this.levelListeners.add(listener);
        return () => { this.levelListeners.delete(listener); };
    };

    levelSnapshot = () => this.level;

    private setLevel(value: number): void {
        if (value === this.level) return;
        this.level = value;
        for (const listener of this.levelListeners) listener();
    }

    get active(): boolean {
        return this.view.status === "connecting" || this.view.status === "live";
    }

    private set(change: Partial<CallView>): void {
        this.view = { ...this.view, ...change };
        for (const listener of this.listeners) listener();
    }

    /** Place the call: `lemma` is who the voice speaks as, `place` where. */
    start = async (provider: VoiceProvider, who: { lemma: string; place: string }) => {
        if (this.active) return;
        const attempt = ++this.attempt;
        this.engineReady = false;
        this.pendingStart = null;
        this.events = new VoiceEvents();
        this.quietUpdates = new Map();
        this.set({ status: "connecting", error: null, cause: null, thinking: false, transcript: [], muted: false });
        this.setLevel(0);
        this.mutedNow = false;
        this.options.engine.open(this.hear);
        this.loadTimer = this.clock.setTimeout(() => {
            if (attempt === this.attempt && !this.engineReady) this.fail("The call’s sound didn’t start. Try calling again.");
        }, LOAD_MS);

        /* Before the session is built, because it goes in the system
           instruction. A Lemma that cannot be read is a call without the
           background: worse, not broken. */
        const context = (await Promise.resolve(this.options.handlers().context?.()).catch(() => "")) ?? "";
        if (attempt !== this.attempt) return;

        this.utterances = new UtteranceBuffer((text) => {
            if (attempt !== this.attempt) return;
            const id = newId();
            this.latestRequest = id;
            for (const quiet of this.events.beginRequest(id)) this.quietUpdates.set(id, quiet);
            void this.options.handlers().route(id, text, this.spoken.history);
        });
        this.pendingStart = { type: "start", provider, instructions: instructionFor(who.lemma, who.place, context) };
        if (this.engineReady) this.sendStart();
    };

    private sendStart(): void {
        const start = this.pendingStart;
        if (!start) return;
        this.pendingStart = null;
        this.options.engine.post(start);
        /* Muted before the page had a call to mute. */
        if (this.mutedNow) this.options.engine.post({ type: "mute", on: true });
    }

    /** Everything the engine says. */
    hear = (message: FromPage) => {
        if (!this.active) return;
        const attempt = this.attempt;
        const now = this.clock.now();
        switch (message.type) {
            case "ready":
                /* The voice clients keep their own deadline for connecting;
                   this one is only for the page itself. */
                this.clock.clearTimeout(this.loadTimer);
                this.loadTimer = null;
                this.engineReady = true;
                this.sendStart();
                return;
            case "open":
                this.set({ status: "live" });
                this.unsubscribe = this.options.handlers().subscribe(this.receiveEvent);
                void this.afterOpen(attempt);
                return;
            case "listening":
                return;
            case "transcript": {
                this.set({ transcript: foldTranscript(this.view.transcript, message) });
                this.spoken = spokenAfter(this.spoken, message);
                if (message.text) this.options.handlers().observeTranscript(this.spoken.history);
                if (message.speaker === "them") {
                    this.lastUser = now;
                    this.utterances?.push(message.text, message.final);
                }
                return;
            }
            case "spoke":
                this.awaitingAudio = 0;
                this.providerBusy = now;
                this.lastVoice = now;
                return;
            case "playing":
                this.playing = message.on;
                if (message.on) {
                    this.awaitingAudio = 0;
                    this.providerBusy = now;
                    this.lastVoice = now;
                }
                return;
            case "level":
                this.setLevel(message.value);
                return;
            case "thinking":
                this.providerBusy = message.on ? now : 0;
                this.set({ thinking: message.on });
                return;
            case "interrupted":
                /* The page has cleared what it was playing. */
                this.providerBusy = 0;
                this.awaitingAudio = 0;
                this.playing = false;
                return;
            case "turn-complete":
                this.providerBusy = 0;
                this.awaitingAudio = 0;
                return;
            case "closed":
                this.end();
                return;
            case "error":
                this.fail(pageProblem(message.message, message.cause, this.options.os ?? "android"), message.cause ?? null);
                return;
        }
    };

    /* The written conversation goes in before the microphone opens, so the
       first thing said lands in context rather than to a voice that knows
       nothing. */
    private async afterOpen(attempt: number): Promise<void> {
        let history = "";
        try {
            history = (await this.options.handlers().history?.()) ?? "";
        } catch {
            history = "";
        }
        if (attempt !== this.attempt) return;
        if (history) this.options.engine.post({ type: "history", text: history });
        this.options.engine.post({ type: "listen" });
    }

    private receiveEvent = (event: VoiceEvent) => {
        if (!this.active) return;
        if (event.kind === "clarify" && event.id !== this.latestRequest) return;
        const quiet = this.events.receive(event);
        if (quiet) this.quietUpdates.set(event.conversationId ?? event.id, quiet);
        else if (event.speak) this.quietUpdates.delete(event.conversationId ?? event.id);
        if ((this.events.waiting || this.quietUpdates.size) && !this.eventTimer) this.eventTimer = this.clock.setTimeout(this.drain, 350);
    };

    private drain = () => {
        this.eventTimer = null;
        if (!this.active || (!this.events.waiting && !this.quietUpdates.size)) return;
        const step = drainStep({
            now: this.clock.now(),
            lastUser: this.lastUser,
            lastVoice: this.lastVoice,
            providerBusy: this.providerBusy,
            awaitingAudio: this.awaitingAudio,
            playing: this.playing,
        }, this.events.needsClarification);
        if (step.kind === "wait") {
            this.eventTimer = this.clock.setTimeout(this.drain, step.ms);
            return;
        }
        if (step.clear) {
            this.options.engine.post({ type: "clear" });
            this.playing = false;
            this.awaitingAudio = 0;
        }
        this.providerBusy = 0;
        for (const quiet of this.quietUpdates.values()) this.options.engine.post({ type: "history", text: quiet });
        this.quietUpdates.clear();
        const update = this.events.take();
        if (update) {
            this.awaitingAudio = this.clock.now();
            this.options.engine.post({ type: "reply", text: update });
        }
        if (this.events.waiting) this.eventTimer = this.clock.setTimeout(this.drain, 350);
    };

    private fail(error: string, cause: PageCause | null = null): void {
        if (!this.active) return;
        this.stop(true);
        this.setLevel(0);
        this.set({ error, cause, status: "error", thinking: false, transcript: [], muted: false });
    }

    /** Hang up. A call that already stopped stays as it stopped. */
    end = () => {
        const running = this.active;
        this.stop(running);
        this.setLevel(0);
        if (running) this.set({ thinking: false, transcript: [], muted: false, status: "ended" });
    };

    private stop(running: boolean): void {
        ++this.attempt;
        this.utterances?.close();
        this.utterances = null;
        this.unsubscribe?.();
        this.unsubscribe = null;
        if (this.eventTimer) this.clock.clearTimeout(this.eventTimer);
        this.eventTimer = null;
        this.clock.clearTimeout(this.loadTimer);
        this.loadTimer = null;
        this.spoken = { history: "", lastSpeaker: null };
        this.awaitingAudio = 0;
        this.providerBusy = 0;
        this.playing = false;
        this.engineReady = false;
        this.pendingStart = null;
        this.mutedNow = false;
        if (running) {
            this.options.engine.post({ type: "end" });
            this.options.engine.close();
        }
    }

    toggleMute = () => {
        if (!this.active) return;
        this.mutedNow = !this.mutedNow;
        this.options.engine.post({ type: "mute", on: this.mutedNow });
        this.set({ muted: this.mutedNow });
    };

    /** Tell the voice a thing is on the screen it is talking over. */
    announceWidget = (label: string) => {
        if (this.active) this.options.engine.post({ type: "widget", label });
    };
}
