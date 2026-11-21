/** The hue a seed paints in, apart from the marks that wear it.
 *
 *  `mark.tsx` draws the cast's live rigs, which load their animation
 *  lazily; a surface that only wants the colour -- a person's initials on
 *  their tint -- takes it from here without pulling the rigs in. `mark.tsx`
 *  re-exports both, so its callers are unchanged. */

function hueOf(seed: string): number {
    let hash = 0;
    for (let index = 0; index < seed.length; index += 1) {
        hash = (hash * 31 + seed.charCodeAt(index)) % 3600;
    }
    return hash / 10;
}

/** The raw hue, for the surfaces that paint something larger than a tile —
 *  the call bar's halo, the profile's banner. */
export function markHue(seed: string): number {
    return hueOf(seed);
}

/** A tile's background and the ink on it, from the same seed: the call
 *  bar's halo, and a person's initials. */
export function markTint(seed: string): { background: string; color: string } {
    const hue = hueOf(seed);
    return {
        background: `hsl(${hue} var(--mark-sat) var(--mark-bg))`,
        color: `hsl(${hue} var(--mark-sat) var(--mark-fg))`,
    };
}
