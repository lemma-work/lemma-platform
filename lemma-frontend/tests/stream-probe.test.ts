import test from "node:test";
import assert from "node:assert/strict";
import {
    DATA_BLOCKS, KEY_SYNC, MAIN_SYNC, STRIP_BLOCKS, fieldsOf, findStrip, lagSince, readStrip,
    type Pixels, type Sync,
} from "../src/computer/stream-probe.ts";

/* The probe page's encoder, written out again rather than imported: the page
   is plain HTML in the sandbox image, and a test sharing the decoder's own
   constants could not tell a layout the two disagree on from one they share. */
function blocksOf(fields: [number, number][]): number[] {
    const bits: number[] = [];
    for (const [value, width] of fields) {
        for (let i = width - 1; i >= 0; i--) bits.push(Math.floor(value / 2 ** i) % 2);
    }
    while (bits.length < DATA_BLOCKS * 3) bits.push(0);
    return Array.from({ length: DATA_BLOCKS }, (_, i) => bits[3 * i]! * 4 + bits[3 * i + 1]! * 2 + bits[3 * i + 2]!);
}

function canvas(width: number, height: number, fill = 255): Pixels {
    const data = new Uint8ClampedArray(width * height * 4).fill(fill);
    return { data, width, height };
}

/** Paint a strip the way the page does, then rough it up the way a lossy
 *  encoder would: every channel nudged by up to `noise`. */
function paint(image: Pixels, x: number, y: number, block: number, sync: Sync, data: number[], noise = 0): void {
    const parity = data.reduce((a, b) => a ^ b, 0);
    const all = [...sync[0], ...data, parity, ...sync[1]];
    all.forEach((value, i) => {
        for (let dy = 0; dy < block; dy++) {
            for (let dx = 0; dx < block; dx++) {
                const at = ((y + dy) * image.width + x + i * block + dx) * 4;
                const jitter = () => (noise ? Math.round((Math.random() * 2 - 1) * noise) : 0);
                image.data[at] = (value & 4 ? 255 : 0) + jitter();
                image.data[at + 1] = (value & 2 ? 255 : 0) + jitter();
                image.data[at + 2] = (value & 1 ? 255 : 0) + jitter();
            }
        }
    });
}

test("a strip under browser chrome is found and decodes to what the page painted", () => {
    const image = canvas(1280, 800);
    const ms = 4_000_000_123;
    paint(image, 3, 87, 16, MAIN_SYNC, blocksOf([[ms, 32], [40_001, 16], [2, 4]]), 60);
    paint(image, 3, 87 + 16, 16, KEY_SYNC, blocksOf([[7, 16], [97, 8], [ms + 5, 32]]), 60);

    const main = findStrip(image, MAIN_SYNC);
    assert.ok(main);
    assert.equal(main.block, 16);
    assert.ok(Math.abs(main.x - 3) <= 2 && Math.abs(main.y - 87) <= 2, JSON.stringify(main));
    assert.deepEqual(fieldsOf(readStrip(image, main, MAIN_SYNC)!, [32, 16, 4]), [ms, 40_001, 2]);

    const key = findStrip(image, KEY_SYNC);
    assert.ok(key);
    assert.deepEqual(fieldsOf(readStrip(image, key, KEY_SYNC)!, [16, 8, 32]), [7, 97, ms + 5]);
});

test("a strip at twice the density is found at its own block size", () => {
    const image = canvas(2560, 400);
    paint(image, 10, 200, 32, MAIN_SYNC, blocksOf([[123, 32], [9, 16], [1, 4]]));
    const place = findStrip(image, MAIN_SYNC);
    assert.equal(place?.block, 32);
    assert.deepEqual(fieldsOf(readStrip(image, place!, MAIN_SYNC)!, [32, 16, 4]), [123, 9, 1]);
});

test("a torn strip is refused rather than decoded to a wrong time", () => {
    const image = canvas(STRIP_BLOCKS * 16, 16);
    paint(image, 0, 0, 16, MAIN_SYNC, blocksOf([[1000, 32], [1, 16], [0, 4]]));
    const place = { x: 0, y: 0, block: 16 };
    assert.ok(readStrip(image, place, MAIN_SYNC));
    /* One data block repainted from the next frame: parity no longer adds up. */
    const torn = canvas(STRIP_BLOCKS * 16, 16);
    torn.data.set(image.data);
    for (let dy = 0; dy < 16; dy++) torn.data.fill(255, (dy * torn.width + 4 * 16) * 4, (dy * torn.width + 5 * 16) * 4);
    assert.equal(readStrip(torn, place, MAIN_SYNC), null);
    /* The other strip's sync is not this one. */
    assert.equal(readStrip(image, place, KEY_SYNC), null);
});

test("nothing that looks like a strip is not mistaken for one", () => {
    assert.equal(findStrip(canvas(800, 200), MAIN_SYNC), null);
    assert.equal(findStrip(canvas(800, 200, 0), MAIN_SYNC), null);
});

test("lag is read across the 2^32 ms wrap, in either direction", () => {
    const two32 = 4294967296;
    const at = 3 * two32 + 50.25;
    assert.equal(lagSince(at, two32 - 30), 80.25);
    assert.equal(lagSince(at, 40), 10.25);
    /* A sandbox clock ahead of this one reads as a negative lag, not a 49-day one. */
    assert.equal(lagSince(at, 70), -19.75);
});
