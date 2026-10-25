/** The call's one socket to the backend.
 *
 *  Microphone audio goes up as binary frames (16 kHz, 16-bit mono PCM) and the
 *  voice comes down the same way at the rate `ready` names; everything else is
 *  JSON. Which voice engine talks, and every agent behind it, is the server's:
 *  nothing here holds a provider's key or knows which provider answered. */

export interface CanvasFrameItem {
    conversation_id: string;
    tool_call_id: string;
    tool_name: string;
    args: unknown;
    result: unknown;
}

export type CallFrame =
    | { type: "ready"; call_id: string; engine: string; sample_rate: number }
    | { type: "transcript"; speaker: "caller" | "voice"; text: string; final: boolean }
    | { type: "clear" }
    | { type: "work"; running: boolean }
    | { type: "canvas.item"; item: CanvasFrameItem; replay?: boolean }
    | { type: "ended"; reason: string | null };

export interface CallStart {
    teammate: string;
    pod: string;
    /** What came before the call, for the voice's brief. */
    context: string;
    engine?: string;
}

export interface CallSocketHandlers {
    onFrame(frame: CallFrame): void;
    onAudio(pcm: ArrayBuffer): void;
    /** The socket closed: `reason` is a sentence for the person, or null for an ordinary end. */
    onClose(reason: string | null): void;
}

export function callSocketUrl(apiUrl: string, podId: string, callId: string, token: string | null): string {
    const root = apiUrl.replace(/\/$/, "").replace(/^http(s?):\/\//, "ws$1://");
    const url = new URL(`${root}/pods/${encodeURIComponent(podId)}/calls/${encodeURIComponent(callId)}/socket`);
    if (token) url.searchParams.set("access_token", token);
    return url.toString();
}

/** How long ending a call waits for the server to save it and close. */
const END_TIMEOUT_MS = 4000;

/** Close codes the server ends a call with, as the person should read them. */
const CLOSE_REASONS: Record<number, string> = {
    4401: "Your session expired. Sign in again to call.",
    4403: "You can no longer reach this pod.",
    4404: "This call no longer exists.",
    4429: "You already have the most calls open at once.",
    4503: "The voice is not available right now.",
};

export function openCallSocket(url: string, start: CallStart, handlers: CallSocketHandlers): CallConnection {
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    let ended = false;
    const end = (reason: string | null) => {
        if (ended) return;
        ended = true;
        handlers.onClose(reason);
    };
    socket.onopen = () => socket.send(JSON.stringify({ type: "start", ...start }));
    socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
            handlers.onAudio(event.data);
            return;
        }
        let frame: unknown;
        try { frame = JSON.parse(String(event.data)); } catch { return; }
        if (!frame || typeof frame !== "object" || typeof (frame as { type?: unknown }).type !== "string") return;
        const typed = frame as CallFrame;
        if (typed.type === "ended") end(typed.reason);
        else handlers.onFrame(typed);
    };
    socket.onclose = (event) => end(event.code === 1000 || event.code === 1005 ? null : CLOSE_REASONS[event.code] ?? (event.reason || "The call dropped."));
    socket.onerror = () => end("The call could not connect.");
    return new CallConnection(socket);
}

export class CallConnection {
    private readonly socket: WebSocket;

    constructor(socket: WebSocket) {
        this.socket = socket;
    }

    private open(): boolean {
        return this.socket.readyState === WebSocket.OPEN;
    }

    sendAudio(pcm: Int16Array): void {
        // Behind on sending means the network cannot keep up; late microphone
        // audio is worse than missing audio, so it is dropped rather than queued.
        if (this.open() && this.socket.bufferedAmount < 64 * 1024) this.socket.send(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength));
    }

    send(control: Record<string, unknown>): void {
        if (this.open()) this.socket.send(JSON.stringify(control));
    }

    /** Ask the server to end the call, and resolve once it has: it saves the
     *  call's transcript before it closes, so whoever reloads the
     *  conversation on this sees it. A server that does not answer in time is
     *  closed on. */
    close(): Promise<void> {
        const socket = this.socket;
        return new Promise((resolve) => {
            if (socket.readyState === WebSocket.CLOSED) { resolve(); return; }
            const done = () => { window.clearTimeout(timer); resolve(); };
            const timer = window.setTimeout(() => { socket.close(1000); resolve(); }, END_TIMEOUT_MS);
            socket.addEventListener("close", done, { once: true });
            if (this.open()) this.send({ type: "end" });
            else socket.close(1000);
        });
    }
}
