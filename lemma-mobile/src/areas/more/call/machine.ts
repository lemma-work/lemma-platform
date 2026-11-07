import type { CallFrame, CallStart } from "@web/call/call-socket";
import { foldTranscript, type CallLine } from "@web/call/call-transcript";
import { canvasItemFrom, type CanvasItem } from "@web/call/canvas";
import { joined, pageProblem, samplesOf, toBase64, type CallStatus, type FromPage, type PageCause, type ToPage } from "./model";
import type { CallLink, CallSource } from "./source";

/** One call, held by the server: the web's `useCall`, as a store the call
 *  screen reads (`useSyncExternalStore`) and the tests drive directly.
 *
 *  The phone is the microphone, the speaker and the screen. Which voice
 *  talks, what the Lemma is working on and what goes on the canvas are all
 *  the server's; this moves audio between the socket and the page, keeps
 *  what is said, and keeps what the Lemma puts on the canvas. */

export interface CallView {
    status: CallStatus;
    error: string | null;
    /** Why the page stopped, when it was the page: a refused microphone is
     *  said as a refusal, not as a dropped call. */
    cause: PageCause | null;
    muted: boolean;
    /** The Lemma has work going for the call. */
    working: boolean;
    transcript: CallLine[];
    canvas: CanvasItem[];
}

/** How long the voice's audio is gathered before one trip to the page. */
export const AUDIO_FLUSH_MS = 60;

const IDLE: CallView = { status: "idle", error: null, cause: null, muted: false, working: false, transcript: [], canvas: [] };

export class CallSession {
    private view: CallView = IDLE;
    private readonly listeners = new Set<() => void>();
    private level = 0;
    private readonly levelListeners = new Set<() => void>();
    private link: CallLink | null = null;
    private attempt = 0;
    private pageReady = false;
    /** The voice's rate, once the server says it is ready. */
    private rate: number | null = null;
    private audio: ArrayBuffer[] = [];
    private flush: ReturnType<typeof setTimeout> | null = null;
    private fade: ReturnType<typeof setTimeout> | null = null;

    constructor(private readonly deps: {
        source: CallSource;
        podId: string;
        /** To the page that holds the sound; nothing when there is none. */
        post: (message: ToPage) => void;
        os: "ios" | "android";
    }) {}

    readonly subscribe = (listener: () => void) => {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    };

    readonly snapshot = () => this.view;

    /** The voice's loudness, 0 to 1, in a store of its own: only the ring
     *  redraws when it moves. */
    readonly subscribeLevel = (listener: () => void) => {
        this.levelListeners.add(listener);
        return () => this.levelListeners.delete(listener);
    };

    readonly levelSnapshot = () => this.level;

    get active(): boolean {
        return this.view.status === "connecting" || this.view.status === "live";
    }

    private set(patch: Partial<CallView>): void {
        this.view = { ...this.view, ...patch };
        for (const listener of this.listeners) listener();
    }

    private setLevel(next: number): void {
        if (next === this.level) return;
        this.level = next;
        for (const listener of this.levelListeners) listener();
    }

    async start(callId: string, start: CallStart): Promise<void> {
        if (this.link) return;
        const attempt = ++this.attempt;
        const current = () => attempt === this.attempt;
        this.rate = null;
        this.set({ ...IDLE, status: "connecting" });
        try {
            const link = await this.deps.source.connect(this.deps.podId, callId, start, {
                onFrame: (frame) => { if (current()) this.frame(frame); },
                onAudio: (pcm) => { if (current()) this.voice(pcm); },
                onClose: (reason) => {
                    if (!current()) return;
                    this.attempt += 1;
                    this.teardown();
                    this.set(reason ? { status: "error", error: reason, working: false } : { status: "ended", working: false });
                },
            });
            if (!current()) {
                link.close();
                return;
            }
            this.link = link;
            /* The page may have loaded first: it is waiting to be told to listen. */
            if (this.pageReady) this.deps.post({ type: "listen" });
        } catch (problem) {
            if (!current()) return;
            this.attempt += 1;
            this.teardown();
            this.set({ status: "error", error: problem instanceof Error && problem.message ? problem.message : "The call could not start." });
        }
    }

    /** What the page says. */
    hear(message: FromPage): void {
        switch (message.type) {
            case "ready":
                this.pageReady = true;
                if (this.link) this.deps.post({ type: "listen" });
                if (this.rate !== null) this.deps.post({ type: "voice", rate: this.rate });
                break;
            case "listening":
                break;
            case "mic":
                if (!this.view.muted && this.link) this.link.sendAudio(samplesOf(message.pcm));
                break;
            case "level":
                this.setLevel(message.value);
                break;
            case "error":
                if (!this.active) return;
                this.attempt += 1;
                this.link?.close();
                this.teardown();
                this.set({ status: "error", error: pageProblem(message.message, message.cause, this.deps.os), cause: message.cause ?? null, working: false });
                break;
        }
    }

    private frame(frame: CallFrame): void {
        switch (frame.type) {
            case "ready":
                this.rate = frame.sample_rate;
                if (this.pageReady) this.deps.post({ type: "voice", rate: frame.sample_rate });
                this.set({ status: "live" });
                break;
            case "transcript":
                this.set({ transcript: foldTranscript(this.view.transcript, { speaker: frame.speaker === "caller" ? "them" : "us", text: frame.text, final: frame.final }) });
                /* With no sound of its own (sample data), the ring follows the words. */
                if (!this.deps.source.sound && frame.speaker === "voice") this.pulse();
                break;
            case "clear":
                /* The caller cut in: what the voice had queued is not said late. */
                this.audio = [];
                this.deps.post({ type: "clear" });
                break;
            case "work":
                this.set({ working: frame.running });
                break;
            case "canvas.item": {
                const item = canvasItemFrom(frame.item);
                if (item && !this.view.canvas.some((one) => one.id === item.id)) this.set({ canvas: [...this.view.canvas, item] });
                break;
            }
            case "ended":
                break;
        }
    }

    private voice(pcm: ArrayBuffer): void {
        this.audio.push(pcm);
        if (this.flush) return;
        this.flush = setTimeout(() => {
            this.flush = null;
            const chunks = this.audio;
            this.audio = [];
            if (chunks.length) this.deps.post({ type: "audio", pcm: toBase64(joined(chunks)) });
        }, AUDIO_FLUSH_MS);
    }

    private pulse(): void {
        this.setLevel(0.85);
        if (this.fade) clearTimeout(this.fade);
        this.fade = setTimeout(() => this.setLevel(0), 220);
    }

    /* The flag first, so a microphone chunk already on its way is dropped. */
    readonly toggleMute = () => {
        const muted = !this.view.muted;
        this.set({ muted });
        this.link?.send({ type: "mute", muted });
        this.deps.post({ type: "mute", on: muted });
    };

    /** The person paged to this canvas item: the voice should know what they see. */
    looking(item: CanvasItem): void {
        this.link?.send({ type: "canvas.focus", tool_call_id: item.id });
    }

    /** An item could not draw on this screen; whoever drew it is told, to fix it. */
    renderFailed(item: CanvasItem, problem: string): void {
        this.link?.send({ type: "canvas.render_failed", tool_call_id: item.id, error: problem });
    }

    readonly end = () => {
        this.attempt += 1;
        const link = this.link;
        this.teardown();
        link?.close();
        if (this.view.status !== "idle") this.set({ status: this.view.status === "error" ? "error" : "ended", working: false, muted: false });
    };

    private teardown(): void {
        this.link = null;
        this.audio = [];
        if (this.flush) clearTimeout(this.flush);
        if (this.fade) clearTimeout(this.fade);
        this.flush = null;
        this.fade = null;
        this.deps.post({ type: "end" });
        /* The page goes with the call: the next call's page says ready again. */
        this.pageReady = false;
        this.setLevel(0);
    }
}
