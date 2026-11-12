/** What the screen's page uses of noVNC 1.7.0 (`@novnc/novnc`, whose
 *  `exports` is its ES module source, `core/rfb.js`, with no types of its
 *  own). Only for checking the page; esbuild ignores it. */
declare module "@novnc/novnc" {
    /** Anything `core/websock.js` `attach` accepts: a WebSocket, an
     *  RTCDataChannel, or an object with the same names. */
    export interface RawChannel {
        binaryType: string;
        protocol: string;
        readyState: number | string;
        onopen: (() => void) | null;
        onmessage: ((event: { data: ArrayBuffer | Uint8Array }) => void) | null;
        onclose: ((event: { code: number; reason: string; wasClean: boolean }) => void) | null;
        onerror: ((event: unknown) => void) | null;
        send(data: Uint8Array | ArrayBuffer): void;
        close(): void;
    }

    export default class RFB extends EventTarget {
        constructor(target: HTMLElement, urlOrChannel: string | RawChannel, options?: { shared?: boolean; credentials?: Record<string, string> });
        viewOnly: boolean;
        focusOnClick: boolean;
        scaleViewport: boolean;
        clipViewport: boolean;
        resizeSession: boolean;
        showDotCursor: boolean;
        background: string;
        qualityLevel: number;
        compressionLevel: number;
        disconnect(): void;
        sendKey(keysym: number, code: string | null, down?: boolean): void;
        clipboardPasteFrom(text: string): void;
        focus(options?: FocusOptions): void;
        blur(): void;
    }
}
