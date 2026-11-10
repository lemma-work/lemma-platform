/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What one viewing session got, from the viewer's side.
 *
 * Bounded everywhere: it comes from a browser and ends up in a log line.
 */
export type StreamBeacon = {
    codec?: ('h264' | 'vp8' | null);
    density?: (number | null);
    fallback?: ('ice-timeout' | 'ice-failed' | 'no-streamer' | 'no-turn' | 'unsupported' | 'error' | null);
    first_frame_ms?: (number | null);
    fps?: (number | null);
    kbps?: (number | null);
    latency_ms?: (number | null);
    relay?: ('udp' | 'tcp' | 'tls' | null);
    seconds: number;
    transport: StreamBeacon.transport;
};
export namespace StreamBeacon {
    export enum transport {
        VNC = 'vnc',
        WEBRTC = 'webrtc',
    }
}
