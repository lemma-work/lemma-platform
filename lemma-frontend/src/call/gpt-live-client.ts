"use client";

// Browser audio/control transport to the app's own server. OpenAI and its key stay
// server-side, exactly as they do for Gemini — the shape of this file is the
// same, and the differences below are the protocol's, not a change of mind.

import type { CallTransport, CallTransportOptions } from "./transport";
import { TranscriptLog, chunkForAppend } from "./live-turns";

/** GPT-Live is 24kHz in and 24kHz out, where Gemini was 16 in and 24 out. */
export const MIC_SAMPLE_RATE = 24000;

type ServerEvent = {
    type?: string;
    delta?: string;
    audio?: string;
    start_ms?: number;
    end_ms?: number;
    offset_ms?: number;
    delegation?: { id?: string; target?: string };
};

export class GptLiveClient implements CallTransport {
    private socket: WebSocket | null = null;
    private disconnected = false;
    private readonly transcript = new TranscriptLog();
    /** The delegation the model has opened and this client has not yet
     *  answered. Replies and progress default to it, so `use-call` can go on
     *  calling `progress(text)` without tracking ids. */
    private current: string | null = null;

    constructor(private readonly options: CallTransportOptions) {}

    async connect(): Promise<void> {
        const url = new URL("/api/live", window.location.href);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(url);
        this.socket = socket;
        await new Promise<void>((resolve, reject) => {
            let ready = false;
            const timeout = setTimeout(() => { reject(new Error("Voice connection timed out.")); socket.close(); }, 20000);
            socket.onopen = () => this.send("start", { instructions: this.options.systemInstruction });
            socket.onmessage = event => {
                let message;
                try { message = JSON.parse(event.data); } catch { return; }
                if (message.type === "ready" && !ready) {
                    ready = true;
                    clearTimeout(timeout);
                    if (this.disconnected) { socket.close(); reject(new Error("Call cancelled.")); return; }
                    this.options.onOpen?.();
                    resolve();
                } else if (message.type === "event") this.handleEvent(message.payload as ServerEvent);
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
                this.socket = null;
                if (!ready) reject(new Error("Voice connection closed before it was ready."));
                else this.options.onClose?.(event.reason || "Call ended");
            };
        });
    }

    private send(type: string, payload: unknown): void {
        const socket = this.socket;
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        if (socket.bufferedAmount > 512 * 1024) { socket.close(1000, "Connection too slow"); return; }
        socket.send(JSON.stringify({ type, payload }));
    }

    private handleEvent(event: ServerEvent): void {
        switch (event.type) {
            case "session.output_audio.delta":
                /* No output-audio-done event exists, and a gap between ranges
                   is silence the server left out rather than a stop — so the
                   player's own draining is what ends a spoken answer here.
                   There is likewise no interruption event: GPT-Live listens
                   and speaks at once and simply stops sending audio, which is
                   why `onInterrupted` never fires on this transport. */
                if (typeof event.audio === "string") this.options.onAudioChunk(event.audio);
                return;

            case "session.input_transcript.delta":
                this.transcript.append({
                    speaker: "them",
                    delta: event.delta ?? "",
                    startMs: event.start_ms ?? 0,
                    endMs: event.end_ms ?? 0,
                });
                this.options.onTranscript?.({ speaker: "them", text: event.delta ?? "" });
                return;

            case "session.output_transcript.delta":
                this.transcript.append({
                    speaker: "us",
                    delta: event.delta ?? "",
                    startMs: event.start_ms ?? 0,
                    endMs: event.end_ms ?? 0,
                });
                /* The same fragments this transport already keeps for working
                   out what a delegation was asking. They were never shown to
                   anyone; now the call screen reads them too. */
                this.options.onTranscript?.({ speaker: "us", text: event.delta ?? "" });
                return;

            case "session.delegation.created": {
                const id = event.delegation?.id;
                if (!id || event.delegation?.target !== "client") return;
                this.current = id;
                this.append("thinking", "An independent router handles this speech. Do not dispatch or repeat the request; await conversation context.", id);
                return;
            }

            default:
                return;
        }
    }

    private append(kind: "instructions" | "thinking" | "commentary", text: string, id: string | null): void {
        for (const content of chunkForAppend(text)) {
            this.send("append", { kind, delegation_id: id, content });
        }
    }

    sendAudioChunk(base64Pcm: string): void {
        this.send("audio", { audio: base64Pcm });
    }

    /* The written conversation goes in as quiet context rather than as
       instructions: it is something to know, not a rule to follow, and
       `thinking` is the append the model is documented not to speak on
       arrival. Same job the Gemini transport used `turnComplete: false` for.
       (`session.input` at session.start would be the tidier home for history,
       but its message shape is not something this was built against.) */
    sendHistory(text: string): void {
        this.append(
            "thinking",
            "Quiet conversation context. Know this state; do not announce it on arrival.\n" + text,
            null,
        );
    }

    /* `commentary` is the one append the model is meant to say out loud. It
       may paraphrase, which is the same latitude the Gemini transport gave
       it, so the teammate's answer is still an answer rather than a script. */
    sendAgentReply(text: string, id?: string): void {
        const delegation = id ?? this.current;
        this.append("commentary", text, delegation);
        if (delegation === this.current) this.current = null;
    }

    /* Same job as Gemini's, through the only channel this model has for
       context it should know and not say. There is no delegation id because a
       widget is not an answer to one — it can appear between delegations, or
       during two at once. */
    sendAgentWidget(label: string): void {
        this.append("thinking", `On the call's screen now, shown and not spoken: ${label}. Refer to it; do not read it out.`, null);
    }

    disconnect(): void {
        this.disconnected = true;
        this.send("close", {});
        this.socket?.close();
        this.socket = null;
    }
}
