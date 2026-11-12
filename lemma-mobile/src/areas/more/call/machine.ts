import type { CallFrame, CallStart } from "@web/call/call-socket";
import { rejoinAfter } from "@web/call/call-rejoin";
import { NOTICE_MS } from "@web/call/call-status";
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
 *  what is said, and keeps what the Lemma puts on the canvas.
 *
 *  A drop nobody chose is not the end of the call: the server still holds
 *  its chat, its work and its canvas, so the same call is joined again
 *  (the web's `rejoinAfter`), keeping what is on screen, the microphone
 *  and mute. The server refusing or ending the call is said in its words. */

export interface CallView {
    status: CallStatus;
    error: string | null;
    /** Why the page stopped, when it was the page: a refused microphone is
     *  said as a refusal, not as a dropped call. */
    cause: PageCause | null;
    /** Something the person should know that the voice is also saying (a
     *  request the Lemma cannot take, and why); the call stays up. Said
     *  once: it clears itself. */
    notice: string | null;
    muted: boolean;
    /** The Lemma has work going for the call. */
    working: boolean;
    transcript: CallLine[];
    canvas: CanvasItem[];
    /** The canvas item on screen, by id; null shows the newest. Held here,
     *  not by the screen: the voice is told which one is up. */
    showing: string | null;
}

/** How long the voice's audio is gathered before one trip to the page. */
export const AUDIO_FLUSH_MS = 60;

const IDLE: CallView = { status: "idle", error: null, cause: null, notice: null, muted: false, working: false, transcript: [], canvas: [], showing: null };

export class CallSession {
    private view: CallView = IDLE;
    private readonly listeners = new Set<() => void>();
    private level = 0;
    private readonly levelListeners = new Set<() => void>();
    private link: CallLink | null = null;
    private attempt = 0;
    private pageReady = false;
    /** The page was told to listen: once a call, so a rejoin keeps the
     *  microphone it has rather than opening it again. */
    private listening = false;
    /** The call being held, for joining it again after a drop. */
    private held: { callId: string; start: CallStart } | null = null;
    /** Times this call has been joined again. */
    private rejoins = 0;
    private rejoin: ReturnType<typeof setTimeout> | null = null;
    /** The voice's rate, once the server says it is ready. */
    private rate: number | null = null;
    private audio: ArrayBuffer[] = [];
    private flush: ReturnType<typeof setTimeout> | null = null;
    private fade: ReturnType<typeof setTimeout> | null = null;
    private noticeTimer: ReturnType<typeof setTimeout> | null = null;

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
        if (this.link || this.rejoin) return;
        this.held = { callId, start };
        this.rejoins = 0;
        this.set({ ...IDLE, status: "connecting" });
        await this.connect(++this.attempt);
    }

    /** Open the call's socket: the first time, or again after a drop. */
    private async connect(attempt: number): Promise<void> {
        const held = this.held;
        if (!held) return;
        const current = () => attempt === this.attempt;
        this.rate = null;
        try {
            /* Muted carries over a rejoin: the server holds to it too. */
            const link = await this.deps.source.connect(this.deps.podId, held.callId, { ...held.start, muted: this.view.muted }, {
                onFrame: (frame) => { if (current()) this.frame(frame); },
                onAudio: (pcm) => { if (current()) this.voice(pcm); },
                onClose: (reason, dropped) => { if (current()) this.closed(reason, dropped); },
            });
            if (!current()) {
                link.close();
                return;
            }
            this.link = link;
            /* The page may have loaded first: it is waiting to be told to listen. */
            if (this.pageReady) this.listen();
        } catch (problem) {
            if (!current()) return;
            /* A rejoin that cannot even begin is one more drop, within the
               bound; the first try failing is the call failing. */
            this.closed(problem instanceof Error && problem.message ? problem.message : "The call could not start.", this.rejoins > 0);
        }
    }

    /** The socket closed, or could not open: join the call again after a
     *  drop, else it is over, with the reason when there is one. */
    private closed(reason: string | null, dropped: boolean): void {
        this.attempt += 1;
        const delay = rejoinAfter(dropped, this.rejoins);
        if (delay === null) {
            this.teardown();
            this.set(reason ? { status: "error", error: reason, working: false } : { status: "ended", working: false });
            return;
        }
        this.rejoins += 1;
        this.dropLink();
        this.set({ status: "connecting", working: false });
        const again = this.attempt;
        this.rejoin = setTimeout(() => {
            this.rejoin = null;
            /* Not if the person ended the call meanwhile. */
            if (again === this.attempt) void this.connect(++this.attempt);
        }, delay);
    }

    private listen(): void {
        if (this.listening) return;
        this.listening = true;
        this.deps.post({ type: "listen" });
    }

    /** What the page says. */
    hear(message: FromPage): void {
        switch (message.type) {
            case "ready":
                this.pageReady = true;
                if (this.link) this.listen();
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
            case "notice":
                this.showNotice(frame.text);
                break;
            case "canvas.item": {
                const item = canvasItemFrom(frame.item);
                if (!item) break;
                const canvas = this.view.canvas.some((one) => one.id === item.id) ? this.view.canvas : [...this.view.canvas, item];
                /* The newest takes the screen, replayed or new: after a
                   rejoin the server tells the voice the newest is up, so the
                   screen has to agree. */
                this.set({ canvas, showing: null });
                break;
            }
            case "ended":
                break;
        }
    }

    private showNotice(text: string): void {
        if (this.noticeTimer) clearTimeout(this.noticeTimer);
        this.set({ notice: text });
        this.noticeTimer = setTimeout(() => {
            this.noticeTimer = null;
            this.set({ notice: null });
        }, NOTICE_MS);
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

    /** The person paged to this item: shown, and the voice told what they see. */
    show(item: CanvasItem): void {
        this.set({ showing: item.id });
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

    /** Only the socket: a rejoin keeps the page, its microphone and mute.
     *  The voice it had queued goes, as the web's player does. */
    private dropLink(): void {
        this.link = null;
        this.audio = [];
        if (this.flush) clearTimeout(this.flush);
        if (this.fade) clearTimeout(this.fade);
        this.flush = null;
        this.fade = null;
        this.deps.post({ type: "clear" });
        this.setLevel(0);
    }

    private teardown(): void {
        this.dropLink();
        if (this.rejoin) clearTimeout(this.rejoin);
        if (this.noticeTimer) clearTimeout(this.noticeTimer);
        this.rejoin = null;
        this.noticeTimer = null;
        if (this.view.notice) this.set({ notice: null });
        this.held = null;
        this.deps.post({ type: "end" });
        /* The page goes with the call: the next call's page says ready again. */
        this.pageReady = false;
        this.listening = false;
    }
}
