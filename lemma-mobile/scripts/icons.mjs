// Draws the app's icons from the Lemma mark -- three rising bars, #5a3fd4,
// never dots -- with no dependency but zlib. Run `node scripts/icons.mjs`
// from lemma-mobile/ after changing the mark; the PNGs are committed.
import { writeFileSync } from "node:fs";
import { deflateSync } from "node:zlib";

const VIOLET = [0x5a, 0x3f, 0xd4];
const WHITE = [0xff, 0xff, 0xff];

/** The mark on its 32-unit grid: x, y, width, height, corner radius. */
const BARS = [
    [4, 19, 5, 10],
    [13, 12, 5, 17],
    [22, 3, 5, 26],
];
const RADIUS = 1.5;

function crc32(bytes) {
    let crc = ~0;
    for (const byte of bytes) {
        crc ^= byte;
        for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
    return ~crc >>> 0;
}

function chunk(type, data) {
    const out = Buffer.alloc(12 + data.length);
    out.writeUInt32BE(data.length, 0);
    out.write(type, 4, "ascii");
    data.copy(out, 8);
    out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
    return out;
}

/** Coverage of a rounded rectangle at a point, 4x4 supersampled. */
function inBar(x, y, [bx, by, bw, bh]) {
    if (x < bx || x > bx + bw || y < by || y > by + bh) return false;
    const cx = Math.min(Math.max(x, bx + RADIUS), bx + bw - RADIUS);
    const cy = Math.min(Math.max(y, by + RADIUS), by + bh - RADIUS);
    return (x - cx) ** 2 + (y - cy) ** 2 <= RADIUS ** 2;
}

/** A square PNG: the mark at `scale` of the canvas, centred, in `ink` on
 *  `ground` (null for transparent). With a ground it is written as plain
 *  RGB: App Store Connect refuses an app icon with an alpha channel, even an
 *  opaque one. */
function draw(size, scale, ink, ground) {
    const channels = ground ? 3 : 4;
    const rows = [];
    const mark = size * scale;
    const offset = (size - mark) / 2;
    for (let py = 0; py < size; py += 1) {
        const row = Buffer.alloc(1 + size * channels);
        for (let px = 0; px < size; px += 1) {
            let hits = 0;
            for (let sy = 0; sy < 4; sy += 1) {
                for (let sx = 0; sx < 4; sx += 1) {
                    const x = ((px + (sx + 0.5) / 4 - offset) / mark) * 32;
                    const y = ((py + (sy + 0.5) / 4 - offset) / mark) * 32;
                    if (BARS.some((bar) => inBar(x, y, bar))) hits += 1;
                }
            }
            const cover = hits / 16;
            const at = 1 + px * channels;
            if (ground) {
                for (let c = 0; c < 3; c += 1) row[at + c] = Math.round(ground[c] * (1 - cover) + ink[c] * cover);
            } else {
                for (let c = 0; c < 3; c += 1) row[at + c] = ink[c];
                row[at + 3] = Math.round(cover * 255);
            }
        }
        rows.push(row);
    }
    const header = Buffer.alloc(13);
    header.writeUInt32BE(size, 0);
    header.writeUInt32BE(size, 4);
    header[8] = 8;
    header[9] = ground ? 2 : 6;
    return Buffer.concat([
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
        chunk("IHDR", header),
        chunk("IDAT", deflateSync(Buffer.concat(rows))),
        chunk("IEND", Buffer.alloc(0)),
    ]);
}

writeFileSync("assets/icon.png", draw(1024, 0.56, VIOLET, WHITE));
/* Adaptive icons crop to a circle or squircle inside the middle 66%. */
writeFileSync("assets/android-icon-foreground.png", draw(1024, 0.4, VIOLET, null));
writeFileSync("assets/android-icon-monochrome.png", draw(1024, 0.4, [0, 0, 0], null));
writeFileSync("assets/splash-icon.png", draw(512, 0.8, VIOLET, null));
writeFileSync("assets/favicon.png", draw(64, 0.8, VIOLET, WHITE));
console.log("icons written");
