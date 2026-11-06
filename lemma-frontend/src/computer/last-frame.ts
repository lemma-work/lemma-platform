/** The last picture this browser saw of the machine's screen, kept for the
 *  next time the pane opens.
 *
 *  Shown at once, under "Connecting…", while the stream comes up — a screen
 *  that opens on the page you left reads as fast even when the first frame
 *  takes a second, and one that opens black reads as broken. It is a picture
 *  of the past and is drawn dimmed, so nobody mistakes it for the live page.
 *
 *  In this browser's storage and nowhere else: it is a screenshot of somebody's
 *  own browser, and it never leaves the device it was taken on. Storage may be
 *  refused or empty — a private window, blocked site data — and then there is
 *  simply no picture.
 */

const KEY = "lemma.screen.last-frame";

/** Wide enough to recognise a page by, small enough to fit in storage. */
const MAX_WIDTH = 960;
const QUALITY = 0.6;

export function loadLastFrame(): string | null {
    try {
        const value = globalThis.localStorage?.getItem(KEY);
        return value?.startsWith("data:image/") ? value : null;
    } catch {
        return null;
    }
}

/** Keep a picture of what `source` shows now. Best effort, and quiet. */
export function saveLastFrame(source: HTMLVideoElement | HTMLCanvasElement | null): void {
    if (!source) return;
    const width = source instanceof HTMLVideoElement ? source.videoWidth : source.width;
    const height = source instanceof HTMLVideoElement ? source.videoHeight : source.height;
    if (!width || !height) return;
    try {
        const scale = Math.min(1, MAX_WIDTH / width);
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.getContext("2d")?.drawImage(source, 0, 0, canvas.width, canvas.height);
        const data = canvas.toDataURL("image/jpeg", QUALITY);
        globalThis.localStorage?.setItem(KEY, data);
    } catch {
        /* A tainted canvas, a full quota, storage refused: no picture next time. */
    }
}

/** The pane's picture, video or noVNC's canvas, wherever it is under `root`. */
export function pictureIn(root: HTMLElement | null): HTMLVideoElement | HTMLCanvasElement | null {
    return root?.querySelector("video") ?? root?.querySelector("canvas") ?? null;
}
