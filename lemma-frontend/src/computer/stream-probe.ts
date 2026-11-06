/** The stream bench's viewer probe: what the live view actually delivers,
 *  read off the picture it draws.
 *
 *  Dev builds only, and only behind `?streamProbe=1` — `live-screen.tsx`
 *  imports this lazily behind both checks, so a production bundle carries none
 *  of it. Nothing here touches the stream: it listens on the socket it is
 *  handed and reads pixels back from noVNC's canvas.
 *
 *  The sandbox shows `lemma-backend/sandbox-images/bench/stream-probe.html`,
 *  which paints its clock into two strips of coloured blocks. Each animation
 *  frame this decodes them and records, per new frame, how far behind the
 *  sandbox's clock the picture is; per key typed into the canvas, when the
 *  page's echo of it came back. `scripts/stream-bench.mjs` reads the results
 *  from `window.__streamProbe` and turns them into numbers.
 *
 *  The layout constants below are the page's; change one and change both.
 */

/** Data blocks per strip, 3 bits each. */
export const DATA_BLOCKS = 20;
/** Sync, data, parity, sync. */
export const STRIP_BLOCKS = 4 + DATA_BLOCKS + 1 + 4;
const TWO32 = 4294967296;

export type Sync = readonly [readonly number[], readonly number[]];
export const MAIN_SYNC: Sync = [[5, 2, 5, 2], [2, 5, 2, 5]];
export const KEY_SYNC: Sync = [[3, 4, 3, 4], [4, 3, 4, 3]];
export const MODES = ["idle", "scroll", "animation", "typing"] as const;

/** Block sizes, in framebuffer pixels, a strip may be painted at: 16 CSS px
 *  on a sandbox display of density 1, 1.5 or 2. */
const BLOCK_SIZES = [16, 24, 32];

export type Pixels = { data: Uint8ClampedArray; width: number; height: number };
export type Place = { x: number; y: number; block: number };

/** A block's 3-bit value: each channel on or off. Thresholded at the middle,
 *  which is far from anything JPEG does to a flat saturated block's centre. */
function colourAt(image: Pixels, x: number, y: number): number {
    let r = 0, g = 0, b = 0;
    for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
            const i = ((y + dy) * image.width + (x + dx)) * 4;
            r += image.data[i]!;
            g += image.data[i + 1]!;
            b += image.data[i + 2]!;
        }
    }
    return (r > 9 * 127 ? 4 : 0) | (g > 9 * 127 ? 2 : 0) | (b > 9 * 127 ? 1 : 0);
}

/** The data blocks of the strip at `place`, or null when what is there is not
 *  a whole strip: wrong sync either end, or a parity that does not add up.
 *  A frame torn mid-update fails here rather than decoding to a wrong time. */
export function readStrip(image: Pixels, place: Place, sync: Sync): number[] | null {
    const { x, y, block } = place;
    const half = Math.floor(block / 2);
    if (x < 0 || y < 0 || x + STRIP_BLOCKS * block > image.width || y + block > image.height) return null;
    const values: number[] = [];
    for (let i = 0; i < STRIP_BLOCKS; i++) values.push(colourAt(image, x + i * block + half, y + half));
    for (let i = 0; i < 4; i++) {
        if (values[i] !== sync[0][i] || values[STRIP_BLOCKS - 4 + i] !== sync[1][i]) return null;
    }
    const data = values.slice(4, 4 + DATA_BLOCKS);
    const parity = data.reduce((a, b) => a ^ b, 0);
    return parity === values[4 + DATA_BLOCKS] ? data : null;
}

/** Unpack data blocks into fields of the given widths, MSB first. */
export function fieldsOf(data: readonly number[], widths: readonly number[]): number[] {
    const bits: number[] = [];
    for (const value of data) bits.push((value >> 2) & 1, (value >> 1) & 1, value & 1);
    const out: number[] = [];
    let at = 0;
    for (const width of widths) {
        let value = 0;
        for (let i = 0; i < width; i++) value = value * 2 + bits[at++]!;
        out.push(value);
    }
    return out;
}

/** Where a strip is in the picture, by scanning for its sync blocks.
 *
 *  The page sits wherever the sandbox browser's window put it, under however
 *  much browser chrome, so its position is found rather than assumed. A
 *  first hit is then centred: sampling a block's middle tolerates a few
 *  pixels of error, but a hit found at a block's edge would not survive the
 *  next frame's compression noise. */
export function findStrip(image: Pixels, sync: Sync): Place | null {
    for (const block of BLOCK_SIZES) {
        const step = Math.max(2, Math.floor(block / 4));
        for (let y = 0; y + block <= image.height; y += step) {
            for (let x = 0; x + STRIP_BLOCKS * block <= image.width; x += step) {
                if (!readStrip(image, { x, y, block }, sync)) continue;
                const fits = (dx: number, dy: number) => readStrip(image, { x: x + dx, y: y + dy, block }, sync) !== null;
                let left = 0, right = 0, up = 0, down = 0;
                while (left > -block && fits(left - 1, 0)) left--;
                while (right < block && fits(right + 1, 0)) right++;
                while (up > -block && fits(0, up - 1)) up--;
                while (down < block && fits(0, down + 1)) down++;
                return { x: x + Math.round((left + right) / 2), y: y + Math.round((up + down) / 2), block };
            }
        }
    }
    return null;
}

/** How far `at` (ms, this machine's clock) is past a sandbox time known only
 *  mod 2^32 ms. Exact while the true gap is under ~24 days either way, and
 *  the clocks of two machines on the same bench are much closer than that. */
export function lagSince(at: number, sandboxMs32: number): number {
    let gap = (Math.floor(at) - sandboxMs32) % TWO32;
    if (gap < 0) gap += TWO32;
    if (gap >= TWO32 / 2) gap -= TWO32;
    return gap + (at - Math.floor(at));
}

/* ---------------------------------------------------------------------- */

type Frame = [at: number, lag: number, frame: number, mode: number];
type Echo = [at: number, count: number, code: number, inputLag: number];
/** One socket's life: made, opened, first decoded picture, closed and how. */
type Connection = [socketAt: number, openAt: number | null, firstFrameAt: number | null, closedAt: number | null, code: number | null];

export type ProbeSnapshot = {
    since: number;
    now: number;
    connect: { socketAt: number; openAt: number | null; firstFrameAt: number | null; extensions: string; protocol: string };
    /** Every socket this page has opened, oldest first, reset or not: a view
     *  that drops and reconnects mid-scenario is a result, not noise. */
    connections: Connection[];
    bytes: { rx: number; tx: number; rxMessages: number; txMessages: number };
    frames: Frame[];
    keysSent: [at: number, code: number][];
    echoes: Echo[];
    canvas: [at: number, width: number, height: number][];
    decode: { ticks: number; main: number; key: number; misses: number; calibrations: number };
};

export type StreamProbe = {
    version: 1;
    live: () => boolean;
    reset: () => void;
    snapshot: () => ProbeSnapshot;
};

declare global {
    interface Window { __streamProbe?: StreamProbe }
}

const MAX_SAMPLES = 50_000;

/** A canvas holding what the viewer shows now: noVNC's own, or for WebRTC a
 *  copy of the video's current frame. The copy is made at the video's own
 *  size, so the strips are read at the stream's pixels either way. */
const videoCopy = typeof document === "undefined" ? null : document.createElement("canvas");
function pictureOf(surface: HTMLElement): HTMLCanvasElement | null {
    const video = surface.querySelector("video");
    if (!video) return surface.querySelector("canvas");
    if (!videoCopy || !video.videoWidth || !video.videoHeight) return null;
    if (videoCopy.width !== video.videoWidth || videoCopy.height !== video.videoHeight) {
        videoCopy.width = video.videoWidth;
        videoCopy.height = video.videoHeight;
    }
    videoCopy.getContext("2d", { willReadFrequently: true })?.drawImage(video, 0, 0);
    return videoCopy;
}

/** Whether this page asked for the probe. The flag is kept for the tab's
 *  session because the shell rewrites its address as it routes, and the
 *  query string does not survive that. */
export function streamProbeAsked(): boolean {
    try {
        if (new URLSearchParams(window.location.search).get("streamProbe") === "1") {
            sessionStorage.setItem("lemma_stream_probe", "1");
            return true;
        }
        return sessionStorage.getItem("lemma_stream_probe") === "1";
    } catch {
        return false;
    }
}

const clock = () => performance.timeOrigin + performance.now();

/** What has been measured since the last reset, across every socket in that
 *  time. Module state, because the pane opens a new socket on every
 *  reconnect and a scenario's numbers must cover all of them. */
const measured = {
    since: clock(),
    bytes: { rx: 0, tx: 0, rxMessages: 0, txMessages: 0 },
    frames: [] as Frame[],
    keysSent: [] as [number, number][],
    echoes: [] as Echo[],
    canvas: [] as [number, number, number][],
    decode: { ticks: 0, main: 0, key: 0, misses: 0, calibrations: 0 },
    /* The key strip's count is the page's, and survives a reconnect. */
    lastCount: -1,
};
const connections: Connection[] = [];

/** Start measuring one connection, adding to what earlier ones measured. */
export function attachStreamProbe({ socket, surface, socketAt, mediaBytes }: {
    socket: WebSocket;
    surface: HTMLElement;
    /** When the socket was made, taken before this module was loaded. */
    socketAt: number;
    /** Over WebRTC the picture is not on the socket: the bytes the peer
     *  connection has received, read from its stats. Counted instead of the
     *  socket's, which carries only signalling. */
    mediaBytes?: () => Promise<number>;
}): void {
    /* Already open when a slow chunk load lands after the handshake: the
       moment is lost, so it reads as unknown rather than as now. */
    const connection: Connection = [socketAt, null, null, null, null];
    connections.push(connection);
    const bytes = measured.bytes;
    const decode = measured.decode;

    socket.addEventListener("open", () => { connection[1] = clock(); });
    socket.addEventListener("close", (event: CloseEvent) => { connection[3] = clock(); connection[4] = event.code; });
    socket.addEventListener("message", (event: MessageEvent) => {
        if (mediaBytes) return;
        const data = event.data as ArrayBuffer | Blob | string;
        bytes.rx += typeof data === "string" ? data.length : data instanceof ArrayBuffer ? data.byteLength : data.size;
        bytes.rxMessages++;
    });
    if (mediaBytes) {
        /* Cumulative from the peer connection, turned into a count since
           the last reset like the socket's. */
        let seen = 0;
        const poll = setInterval(() => {
            if (socket.readyState > WebSocket.OPEN) { clearInterval(poll); return; }
            void mediaBytes().then((total) => {
                if (total > seen) { bytes.rx += total - seen; bytes.rxMessages++; }
                seen = total;
            }).catch(() => undefined);
        }, 500);
    }
    /* Counted by wrapping this one socket's `send`, not the prototype's: the
       call goes through unchanged, and no other socket on the page is seen. */
    const send = socket.send.bind(socket);
    socket.send = (data) => {
        bytes.tx += typeof data === "string" ? data.length : "byteLength" in data ? data.byteLength : data.size;
        bytes.txMessages++;
        send(data);
    };

    /* The moment a key went in, from the event's own timestamp rather than when
       this listener ran. Only keys aimed at the picture count. */
    const onKey = (event: KeyboardEvent) => {
        if (event.key.length !== 1 || !surface.contains(document.activeElement)) return;
        if (measured.keysSent.length < MAX_SAMPLES) measured.keysSent.push([performance.timeOrigin + event.timeStamp, event.key.charCodeAt(0) % 256]);
    };
    document.addEventListener("keydown", onKey, true);

    let main: Place | null = null;
    let key: Place | null = null;
    let lastCalibration = 0;
    let lastFrame = -1;
    let size = "";
    let context: CanvasRenderingContext2D | null = null;
    let canvas: HTMLCanvasElement | null = null;

    const tick = () => {
        if (socket.readyState > WebSocket.OPEN) { document.removeEventListener("keydown", onKey, true); return; }
        requestAnimationFrame(tick);
        decode.ticks++;
        const found = pictureOf(surface);
        if (!found || found.width === 0 || found.height === 0) return;
        if (found !== canvas) { canvas = found; context = found.getContext("2d", { willReadFrequently: true }); }
        if (!context) return;
        const at = clock();
        const now = found.width + "x" + found.height;
        if (now !== size) {
            size = now;
            measured.canvas.push([at, found.width, found.height]);
            main = key = null;
        }

        if ((!main || !key) && at - lastCalibration > 250) {
            lastCalibration = at;
            decode.calibrations++;
            const whole = context.getImageData(0, 0, found.width, found.height);
            main = main ?? findStrip(whole, MAIN_SYNC);
            key = key ?? findStrip(whole, KEY_SYNC);
        }

        const read = (place: Place, sync: Sync) => {
            const region = context!.getImageData(place.x, place.y, STRIP_BLOCKS * place.block, place.block);
            return readStrip(region, { x: 0, y: 0, block: place.block }, sync);
        };

        if (main) {
            const data = read(main, MAIN_SYNC);
            if (!data) {
                decode.misses++;
                main = null;
            } else {
                decode.main++;
                const [ms32, frame, mode] = fieldsOf(data, [32, 16, 4]) as [number, number, number];
                if (frame !== lastFrame) {
                    lastFrame = frame;
                    connection[2] ??= at;
                    if (measured.frames.length < MAX_SAMPLES) measured.frames.push([at, lagSince(at, ms32), frame, mode]);
                }
            }
        }
        if (key) {
            const data = read(key, KEY_SYNC);
            if (!data) {
                key = null;
            } else {
                decode.key++;
                const [count, code, ms32] = fieldsOf(data, [16, 8, 32]) as [number, number, number];
                if (count !== measured.lastCount) {
                    measured.lastCount = count;
                    /* Negative: the sandbox received the key this long after
                       it was sent, on uncorrected clocks. The runner adds the
                       clock offset; on one machine there is none. */
                    const sent = measured.keysSent[count - 1];
                    const inputLag = sent ? -lagSince(sent[0], ms32) : Number.NaN;
                    if (count > 0 && measured.echoes.length < MAX_SAMPLES) measured.echoes.push([at, count, code, inputLag]);
                }
            }
        }
    };
    requestAnimationFrame(tick);

    window.__streamProbe = {
        version: 1,
        live: () => socket.readyState === WebSocket.OPEN && connection[2] !== null,
        reset: () => {
            measured.since = clock();
            bytes.rx = bytes.tx = bytes.rxMessages = bytes.txMessages = 0;
            measured.frames = [];
            measured.keysSent = [];
            measured.echoes = [];
            measured.canvas = [];
            measured.lastCount = -1;
            decode.ticks = decode.main = decode.key = decode.misses = decode.calibrations = 0;
        },
        snapshot: () => ({
            since: measured.since,
            now: clock(),
            connect: { socketAt, openAt: connection[1], firstFrameAt: connection[2], extensions: socket.extensions, protocol: socket.protocol },
            connections: connections.map((c) => [...c] as Connection),
            bytes: { ...bytes },
            frames: measured.frames,
            keysSent: measured.keysSent,
            echoes: measured.echoes,
            canvas: measured.canvas,
            decode: { ...decode },
        }),
    };
}
