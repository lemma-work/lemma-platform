"use client";

// Browser audio/control transport to the app's own server. Gemini and its key stay server-side.

import type { CallTransport, CallTransportOptions } from "./transport";

/** Gemini Live is 16kHz in and 24kHz out. */
export const MIC_SAMPLE_RATE = 16000;


// Structural type for the session object @google/genai's ai.live.connect()
// resolves to — kept narrow to just what this file calls. Verified against
// the installed SDK's own genai.d.ts (LiveSendClientContentParameters /
// LiveSendRealtimeInputParameters), not guessed from docs.
interface LiveSession {
    sendRealtimeInput(input: { audio: { data: string; mimeType: string } }): void;
    // Unlike sendRealtimeInput, this supports turnComplete: false — content
    // that joins the model's context WITHOUT prompting a response. That's the
    // whole reason context updates use this instead of sendRealtimeInput's
    // text field, which always nudges toward an immediate reply.
    sendClientContent(input: { turns: string; turnComplete: boolean }): void;
    close(): void;
}

export class GeminiLiveClient implements CallTransport {
    private session: LiveSession | null = null;
    private socket: WebSocket | null = null;
    private disconnected = false;
    constructor(private readonly options: CallTransportOptions) {}

    async connect(): Promise<void> {
        const url = new URL("/api/voice", window.location.href);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(url);
        this.socket = socket;
        await new Promise<void>((resolve, reject) => {
            let ready = false;
            const timeout = setTimeout(() => { reject(new Error("Voice connection timed out.")); socket.close(); }, 20000);
            const send = (type: string, payload: unknown) => {
                if (socket.readyState !== WebSocket.OPEN) return;
                if (socket.bufferedAmount > 512 * 1024) { socket.close(1000, "Connection too slow"); return; }
                socket.send(JSON.stringify({ type, payload }));
            };
            socket.onopen = () => send("start", { systemInstruction: this.options.systemInstruction });
            socket.onmessage = event => {
                let message;
                try { message = JSON.parse(event.data); } catch { return; }
                if (message.type === "ready" && !ready) {
                    ready = true;
                    clearTimeout(timeout);
                    if (this.disconnected) { socket.close(); reject(new Error("Call cancelled.")); return; }
                    this.session = {
                        sendRealtimeInput: payload => send("audio", payload),
                        sendClientContent: payload => send("content", payload),
                        close: () => socket.close(),
                    };
                    this.options.onOpen?.();
                    resolve();
                } else if (message.type === "event") this.handleMessage(message.payload);
                else if (message.type === "error") {
                    const error = new Error(message.message || "Voice connection failed.");
                    if (!ready) { clearTimeout(timeout); reject(error); }
                    else this.options.onError?.(error);
                }
            };
            socket.onerror = () => {
                const error = new Error("Could not connect to the voice server.");
                if (!ready) { clearTimeout(timeout); reject(error); }
                else this.options.onError?.(error);
            };
            socket.onclose = event => {
                clearTimeout(timeout);
                this.session = null;
                if (!ready) reject(new Error("Voice connection closed before it was ready."));
                else if (!this.disconnected && event.code !== 1000) this.options.onError?.(new Error(event.reason || `Voice connection closed (${event.code}).`));
                else this.options.onClose?.(event.reason || "Call ended");
            };
        });
    }

    private handleMessage(message: unknown): void {
        const payload = message as {
            serverContent?: {
                interrupted?: boolean;
                turnComplete?: boolean;
                interactionStatus?: string;
                inputTranscription?: { text?: string; finished?: boolean };
                outputTranscription?: { text?: string; finished?: boolean };
                modelTurn?: { parts?: Array<{ inlineData?: { data?: string; mimeType?: string } }> };
            };
        };

        if (payload.serverContent?.interrupted) this.options.onInterrupted?.();

        /* Idle is read from `interactionStatus`, not from `turnComplete`.
           Under extended thinking the model can close a turn and keep
           reasoning behind it, so the two states came apart: Gemini's own
           docs say `turnComplete: true` does not mean the session is idle
           and to read `interactionStatus` instead. This is the field that
           still distinguishes them, and it is what the call bar needs to say
           whether the teammate is still working. */
        /* Two fields, one for each direction. `interimInputTranscription` is
           deliberately not read: it is the same words again, revised, and
           folding both would print the person's sentence twice. */
        const heard = payload.serverContent?.inputTranscription;
        if (heard?.text || heard?.finished) {
            this.options.onTranscript?.({ speaker: "them", text: heard.text ?? "", final: heard.finished });
        }
        const said = payload.serverContent?.outputTranscription;
        if (said?.text || said?.finished) {
            this.options.onTranscript?.({ speaker: "us", text: said.text ?? "", final: said.finished });
        }

        const status = payload.serverContent?.interactionStatus;

        const parts = payload.serverContent?.modelTurn?.parts ?? [];
        for (const part of parts) {
            const audio = part.inlineData;
            if (audio?.data && audio.mimeType?.startsWith("audio/")) {
                this.options.onAudioChunk(audio.data);
            }
        }

        // Apply lifecycle state after audio in the same frame. Extended
        // thinking stays busy even when that frame closes an audio turn.
        if (payload.serverContent?.turnComplete) this.options.onTurnComplete?.();
        if (status === "IN_PROGRESS" || status === "IDLE") this.options.onThinking?.(status === "IN_PROGRESS");
    }

    sendAudioChunk(base64Pcm: string): void {
        this.session?.sendRealtimeInput({ audio: { data: base64Pcm, mimeType: "audio/pcm;rate=16000" } });
    }

    /** Silent context: snapshots, accepted work, history and routine events. */
    sendHistory(text: string): void {
        this.session?.sendClientContent({
            turns: `[Quiet conversation context. Know this state; do not announce it on arrival.]\n${text}`,
            turnComplete: false,
        });
    }

    /** Called at a conversational pause by the event queue. */
    sendAgentReply(text: string): void {
        this.session?.sendClientContent({ turns: text, turnComplete: true });
    }

    sendAgentWidget(label: string): void {
        this.session?.sendClientContent({
            turns: `[On the call's screen now, shown to the person and not spoken: ${label}. Refer to it; do not read it out.]`,
            turnComplete: false,
        });
    }



    disconnect(): void {
        this.disconnected = true;
        this.socket?.close();
        this.socket = null;
        this.session?.close();
        this.session = null;
    }
}
