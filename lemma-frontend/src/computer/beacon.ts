/** What one viewing session got, said to the API once when it ends.
 *
 *  Nothing on the server sees a frame arrive — not the API, not the relay —
 *  so the frame rate, the latency and why a pane fell back to VNC exist only
 *  here. This keeps a running account while the screen is open and turns it
 *  into the one report `POST /machines/me/browser:beacon` takes.
 */

import type { StreamStats } from "./rtc-session";
import type { FallbackReason, Transport } from "./stream";

export interface Beacon {
    transport: Transport;
    fallback?: FallbackReason;
    seconds: number;
    first_frame_ms?: number;
    fps?: number;
    latency_ms?: number;
    kbps?: number;
    codec?: "h264" | "vp8";
    relay?: "udp" | "tcp" | "tls";
    density?: number;
}

export class SessionReport {
    private readonly started: number;
    private firstFrameAt: number | null = null;
    private samples: StreamStats[] = [];
    private fallback: FallbackReason | undefined;
    private transport: Transport;
    private readonly density: number;

    constructor(transport: Transport, density: number, now = Date.now()) {
        this.transport = transport;
        this.density = density;
        this.started = now;
    }

    firstFrame(now = Date.now()): void {
        this.firstFrameAt ??= now;
    }

    fellBack(reason: FallbackReason): void {
        this.fallback = reason;
        this.transport = "vnc";
        /* The VNC part is its own session as far as numbers go: WebRTC's
           samples say nothing about it. */
        this.samples = [];
    }

    sample(stats: StreamStats): void {
        if (this.samples.length < 1_000) this.samples.push(stats);
    }

    /** The report, or null when the screen was never on long enough to say
     *  anything — a pane opened and closed in the same breath. */
    beacon(now = Date.now()): Beacon | null {
        const seconds = (now - this.started) / 1000;
        if (seconds < 1) return null;
        const report: Beacon = { transport: this.transport, seconds: round(seconds), density: this.density };
        if (this.fallback) report.fallback = this.fallback;
        if (this.firstFrameAt !== null) report.first_frame_ms = Math.round(this.firstFrameAt - this.started);
        const fps = median(this.samples.map((s) => s.fps));
        const latency = median(this.samples.map((s) => s.latencyMs));
        const kbps = median(this.samples.map((s) => s.kbps));
        if (fps !== null) report.fps = round(fps);
        if (latency !== null) report.latency_ms = Math.round(latency);
        if (kbps !== null) report.kbps = Math.round(kbps);
        const last = this.samples.at(-1);
        if (last?.codec) report.codec = last.codec;
        if (last?.relay) report.relay = last.relay;
        return report;
    }
}

function median(values: (number | null)[]): number | null {
    const known = values.filter((v): v is number => v !== null && Number.isFinite(v)).sort((a, b) => a - b);
    if (!known.length) return null;
    const middle = Math.floor(known.length / 2);
    return known.length % 2 ? known[middle]! : (known[middle - 1]! + known[middle]!) / 2;
}

function round(value: number): number {
    return Math.round(value * 10) / 10;
}
