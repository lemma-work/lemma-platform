/** One WebRTC connection to a machine's screen, without the pane around it.
 *
 *  The API's view socket with `transport=rtc` carries no picture: it carries
 *  the offer, the answer and ICE candidates between this browser and the
 *  streamer in the machine, and this class drives that exchange. The picture
 *  and the input go over the peer connection itself, relayed through TURN —
 *  both ends are relay-only, so this browser never learns the machine's
 *  address and the machine never learns this one's.
 *
 *  What this side may send on the socket is five messages — offer, candidate,
 *  take, release, bye — and nothing it sends can name a mode. Asking to drive
 *  is `take`; the API decides and gives the streamer a ticket for it. The
 *  streamer is the one that then accepts this browser's `input` channel, or
 *  closes it unread.
 */

import { ICE_DEADLINE_MS, type FallbackReason, type InputMessage } from "./stream";

export interface RtcConfig {
    t: "config";
    ice_servers: RTCIceServer[];
    policy: "relay" | "all";
    mode: "view" | "control";
    may_control: boolean;
}

export interface CursorShape { id: number; x: number; y: number; w?: number; h?: number; png?: string }

export interface RtcHandlers {
    /** The video, once a track arrives. */
    onStream(stream: MediaStream): void;
    /** Media is flowing over a connected peer. */
    onLive(): void;
    /** Whether this pane is driving right now, and whether it may. */
    onControl(driving: boolean, mayDrive: boolean): void;
    onCursor(shape: CursorShape): void;
    onPointer(x: number, y: number): void;
    onScreenSize(width: number, height: number): void;
    onClipboard(text: string): void;
    onAgentDriving(driving: boolean): void;
    /** WebRTC did not work here; the pane should use VNC. */
    onFallback(reason: FallbackReason): void;
    /** The socket closed with a code the pane reads (`live.ts`). */
    onClosed(code: number): void;
}

export interface StreamStats {
    fps: number | null;
    kbps: number | null;
    latencyMs: number | null;
    codec: "h264" | "vp8" | null;
    relay: "udp" | "tcp" | "tls" | null;
}

/** The close code that means "use VNC": no streamer in the image, no TURN. */
export const CLOSE_USE_VNC = 4427;

export class RtcSession {
    private socket: WebSocket;
    private peer: RTCPeerConnection | null = null;
    private input: RTCDataChannel | null = null;
    private mayDrive = false;
    private driving = false;
    private connected = false;
    private finished = false;
    private deadline: ReturnType<typeof setTimeout> | undefined;
    private lastBytes = 0;
    private lastStatsAt = 0;
    private readonly handlers: RtcHandlers;

    constructor(url: string, handlers: RtcHandlers) {
        this.handlers = handlers;
        this.socket = new WebSocket(url);
        this.socket.addEventListener("message", (event) => {
            if (typeof event.data === "string") void this.onSignal(event.data);
        });
        this.socket.addEventListener("close", (event) => {
            if (this.finished) return;
            if (event.code === CLOSE_USE_VNC) return this.fallBack("no-streamer");
            /* A close after the peer connected is the socket's business, not
               the picture's: media goes over the peer, which carries on. */
            if (!this.connected) {
                this.finished = true;
                clearTimeout(this.deadline);
                handlers.onClosed(event.code);
            }
        });
    }

    /** Ask to drive. The answer arrives as `onControl`. */
    take(): void {
        this.signal({ t: "take" });
    }

    /** Stop driving and watch. */
    release(): void {
        this.signal({ t: "release" });
        this.input?.close();
        this.input = null;
    }

    /** One input message, if this pane is driving and the channel is open. */
    send(message: InputMessage): boolean {
        if (!this.driving || this.input?.readyState !== "open") return false;
        this.input.send(JSON.stringify(message));
        return true;
    }

    get isDriving(): boolean {
        return this.driving;
    }

    /** The signalling socket, for the stream bench's probe only. */
    get signalling(): WebSocket {
        return this.socket;
    }

    /** Media bytes received so far, for the stream bench's probe only. */
    async bytesReceived(): Promise<number> {
        let total = 0;
        (await this.peer?.getStats())?.forEach((entry: { type: string; kind?: string; bytesReceived?: number }) => {
            if (entry.type === "inbound-rtp" && entry.kind === "video") total += entry.bytesReceived ?? 0;
        });
        return total;
    }

    close(): void {
        if (this.finished) return;
        this.finished = true;
        clearTimeout(this.deadline);
        this.signal({ t: "bye" });
        this.peer?.close();
        try { this.socket.close(1000); } catch { /* already closed */ }
    }

    /** What the stream is delivering now, for the beacon and the probe. */
    async stats(): Promise<StreamStats> {
        const empty: StreamStats = { fps: null, kbps: null, latencyMs: null, codec: null, relay: null };
        if (!this.peer) return empty;
        const report = await this.peer.getStats();
        const found = { ...empty };
        const codecs = new Map<string, string>();
        let pairId: string | undefined;
        report.forEach((entry: Record<string, unknown> & { type: string; id: string }) => {
            if (entry.type === "codec") codecs.set(entry.id, String(entry.mimeType ?? ""));
            if (entry.type === "transport" && typeof entry.selectedCandidatePairId === "string") pairId = entry.selectedCandidatePairId;
        });
        report.forEach((entry: Record<string, unknown> & { type: string; id: string }) => {
            if (entry.type === "inbound-rtp" && entry.kind === "video") {
                found.fps = typeof entry.framesPerSecond === "number" ? entry.framesPerSecond : null;
                const mime = codecs.get(String(entry.codecId ?? "")) ?? "";
                found.codec = /h264/i.test(mime) ? "h264" : /vp8/i.test(mime) ? "vp8" : null;
                const bytes = Number(entry.bytesReceived ?? 0);
                const now = Number(entry.timestamp ?? 0);
                if (this.lastStatsAt && now > this.lastStatsAt) {
                    found.kbps = ((bytes - this.lastBytes) * 8) / (now - this.lastStatsAt);
                }
                this.lastBytes = bytes;
                this.lastStatsAt = now;
                const emitted = Number(entry.jitterBufferEmittedCount ?? 0);
                if (emitted > 0) found.latencyMs = (Number(entry.jitterBufferDelay ?? 0) / emitted) * 1000;
            }
            if (entry.type === "candidate-pair" && entry.id === pairId) {
                const rtt = Number(entry.currentRoundTripTime ?? NaN);
                if (!Number.isNaN(rtt)) found.latencyMs = (found.latencyMs ?? 0) + (rtt * 1000) / 2;
                const local = report.get(String(entry.localCandidateId ?? "")) as { relayProtocol?: string; protocol?: string } | undefined;
                const protocol = local?.relayProtocol ?? local?.protocol;
                found.relay = protocol === "udp" || protocol === "tcp" || protocol === "tls" ? protocol : null;
            }
        });
        return found;
    }

    private signal(message: Record<string, unknown>): void {
        if (this.socket.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
    }

    private fallBack(reason: FallbackReason): void {
        if (this.finished) return;
        this.finished = true;
        clearTimeout(this.deadline);
        this.peer?.close();
        try { this.socket.close(1000); } catch { /* already closed */ }
        this.handlers.onFallback(reason);
    }

    private async onSignal(raw: string): Promise<void> {
        let message: Record<string, unknown>;
        try {
            message = JSON.parse(raw) as Record<string, unknown>;
        } catch {
            return;
        }
        try {
            if (message.t === "config") await this.start(message as unknown as RtcConfig);
            else if (message.t === "answer" && typeof message.sdp === "string") {
                await this.peer?.setRemoteDescription({ type: "answer", sdp: message.sdp });
            } else if (message.t === "candidate" && this.peer) {
                if (typeof message.candidate === "string") {
                    await this.peer.addIceCandidate({
                        candidate: message.candidate,
                        sdpMid: (message.sdpMid as string | null) ?? null,
                        sdpMLineIndex: (message.sdpMLineIndex as number | null) ?? null,
                    });
                }
            } else if (message.t === "mode" || message.t === "ready") {
                this.setDriving(message.mode === "control");
            } else if (message.t === "error") {
                this.fallBack("error");
            }
        } catch {
            this.fallBack("error");
        }
    }

    private async start(config: RtcConfig): Promise<void> {
        /* The ICE deadline starts here, with the machine's config, and not
           when the socket opens. Everything before this message is the
           machine starting its browser — a cold start takes longer than the
           whole deadline — and timing that sent every cold start to VNC for
           no network reason at all. What the deadline is for is ICE, and ICE
           begins now. A machine that never gets this far is not left
           hanging: the server closes the socket with a code the pane reads. */
        clearTimeout(this.deadline);
        this.deadline = setTimeout(() => {
            if (!this.connected) this.fallBack("ice-timeout");
        }, ICE_DEADLINE_MS);
        this.mayDrive = config.may_control;
        const peer = new RTCPeerConnection({
            iceServers: config.ice_servers,
            iceTransportPolicy: config.policy === "relay" ? "relay" : "all",
        });
        this.peer = peer;
        const transceiver = peer.addTransceiver("video", { direction: "recvonly" });
        preferCodecs(transceiver);
        const events = peer.createDataChannel("events", { ordered: true });
        events.addEventListener("message", (event) => {
            if (typeof event.data === "string") this.onEvent(event.data);
        });
        if (config.mode === "control") this.openInput();
        peer.addEventListener("track", (event) => {
            this.handlers.onStream(event.streams[0] ?? new MediaStream([event.track]));
        });
        peer.addEventListener("icecandidate", (event) => {
            this.signal({
                t: "candidate",
                candidate: event.candidate?.candidate ?? null,
                sdpMid: event.candidate?.sdpMid ?? null,
                sdpMLineIndex: event.candidate?.sdpMLineIndex ?? null,
            });
        });
        peer.addEventListener("connectionstatechange", () => {
            if (peer.connectionState === "connected" && !this.connected) {
                this.connected = true;
                clearTimeout(this.deadline);
                this.handlers.onLive();
            }
            if (peer.connectionState === "failed") {
                if (this.connected) this.handlers.onClosed(1006);
                else this.fallBack("ice-failed");
            }
        });
        const offer = await peer.createOffer();
        await peer.setLocalDescription(offer);
        this.signal({ t: "offer", sdp: peer.localDescription?.sdp ?? offer.sdp });
    }

    /** The input channel, created on this side once driving is granted.
     *
     *  No renegotiation: the `events` channel already set up the SCTP
     *  association, and a later channel opens over it in-band. */
    private openInput(): void {
        if (!this.peer || this.input) return;
        this.input = this.peer.createDataChannel("input", { ordered: true });
    }

    private setDriving(driving: boolean): void {
        this.driving = driving;
        if (driving) this.openInput();
        else {
            this.input?.close();
            this.input = null;
        }
        this.handlers.onControl(driving, this.mayDrive);
    }

    private onEvent(raw: string): void {
        let event: Record<string, unknown>;
        try {
            event = JSON.parse(raw) as Record<string, unknown>;
        } catch {
            return;
        }
        const number = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
        switch (event.t) {
            case "hello":
            case "size": {
                const width = number(event.w), height = number(event.h);
                if (width && height) this.handlers.onScreenSize(width, height);
                break;
            }
            case "cursor": {
                const id = number(event.id), x = number(event.x), y = number(event.y);
                if (id === null || x === null || y === null) break;
                this.handlers.onCursor({
                    id, x, y,
                    w: number(event.w) ?? undefined,
                    h: number(event.h) ?? undefined,
                    png: typeof event.png === "string" ? event.png : undefined,
                });
                break;
            }
            case "pos": {
                const x = number(event.x), y = number(event.y);
                if (x !== null && y !== null) this.handlers.onPointer(x, y);
                break;
            }
            case "control":
                this.setDriving(event.you === true);
                break;
            case "revoked":
                this.setDriving(false);
                break;
            case "clip":
                if (typeof event.s === "string") this.handlers.onClipboard(event.s);
                break;
            case "agent":
                this.handlers.onAgentDriving(event.driving === true);
                break;
        }
    }
}

/** H.264 constrained baseline first, then VP8: the two every browser we
 *  support decodes in hardware or cheaply, iOS Safari included. Anything else
 *  is left out so the streamer never has to say no to it. */
function preferCodecs(transceiver: RTCRtpTransceiver): void {
    const capabilities = globalThis.RTCRtpReceiver?.getCapabilities?.("video");
    if (!capabilities || typeof transceiver.setCodecPreferences !== "function") return;
    const baseline = capabilities.codecs.filter((codec) =>
        codec.mimeType.toLowerCase() === "video/h264"
        && /profile-level-id=42e01f/i.test(codec.sdpFmtpLine ?? "")
        && /packetization-mode=1/.test(codec.sdpFmtpLine ?? ""));
    const vp8 = capabilities.codecs.filter((codec) => codec.mimeType.toLowerCase() === "video/vp8");
    const preferred = [...baseline, ...vp8];
    if (preferred.length) {
        try { transceiver.setCodecPreferences(preferred); } catch { /* the browser keeps its own order */ }
    }
}
