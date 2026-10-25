/** A Lemma's hue, with no React Native in it, so the face (`face.tsx`) and
 *  its colour (`identity.ts`) share it and a test can reach it. */

/** `markHue`, verbatim. */
export function hueOf(seed: string): number {
    let hash = 0;
    for (let index = 0; index < seed.length; index += 1) {
        hash = (hash * 31 + seed.charCodeAt(index)) % 3600;
    }
    return hash / 10;
}

/** Hue bands, in degrees, that belong to the signals. The warm one is wider
 *  than amber itself: at a button's depth, every hue from red-orange to olive
 *  reads as brown, and brown reads as amber's "behind" at a glance. */
export const RESERVED: readonly (readonly [number, number])[] = [
    [245, 292],
    [8, 80],
];
/** How far past a band a moved hue lands, and over how many degrees the
 *  band's half is spread there, so moved Lemmas still differ from each other. */
const MARGIN = 14;
const SPREAD = 20;

export function identityHue(id: string): number {
    let hue = hueOf(id);
    for (const [from, to] of RESERVED) {
        if (hue < from || hue > to) continue;
        const mid = (from + to) / 2;
        hue = hue < mid
            ? from - MARGIN - SPREAD + ((hue - from) / (mid - from)) * SPREAD
            : to + MARGIN + ((hue - mid) / (to - mid)) * SPREAD;
        break;
    }
    return (hue + 360) % 360;
}
