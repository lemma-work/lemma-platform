/**
 * The identity palette, frozen as literals.
 *
 * `styles/features/resource-identity.css` is where these live for the product,
 * as custom properties, so a body can re-colour itself when the appearance
 * changes without re-rendering — the thing that file calls out as the reason a
 * PNG mascot was never good enough.
 *
 * Nothing outside the app can read them. A contact photo is looked at inside
 * someone's address book, and the being it draws has to arrive as pixels with
 * its colour already in it: no stylesheet, no `currentColor`, no `.dark` class
 * to answer to. `social-card.ts` hit the same wall on the edge and answered it
 * the same way — frozen literals, stated as a deliberate exception rather than
 * smuggled in as a default.
 *
 * **Light values only, on purpose.** A saved photo is a file, not a themed
 * surface: it is copied into a contact app that paints whatever ground it likes
 * and never tells us which. Shipping the dark ramp instead would put a `#8b7af5`
 * body on a white contact card, which is the same mistake as `#e6e0ff` on
 * `#131311` — the one the stylesheet already learned not to make.
 *
 * `identity-palette.test.ts` reads the stylesheet and compares, so a tone that
 * moves there cannot leave a stale twin here.
 */

/** Where the values above are published for the product. Read by the test. */
export const IDENTITY_STYLESHEET = 'styles/features/resource-identity.css';

/** Jewel tones, indexed by `IdentityGenes.tone`. */
export const IDENTITY_TONES: readonly string[] = [
    '#5a3fd4',
    '#11743c',
    '#8a6400',
    '#c22f15',
    '#d97757',
];

/** The tinted grounds a mark sits on, indexed the same way. */
export const IDENTITY_SOFTS: readonly string[] = [
    '#e6e0ff',
    '#d9f5e3',
    '#f7edd4',
    '#ffe1da',
    '#f2ebe6',
];

export const IDENTITY_SCLERA = '#fdfcf8';
export const IDENTITY_PUPIL = '#2b2924';
/** Darkens whatever tone sits under it, so one value serves all five. */
export const IDENTITY_SHADE = '#000000';

/** The tone a set of genes draws in, wrapped so an out-of-range index cannot blank a face. */
export function toneColor(tone: number): string {
    return IDENTITY_TONES[tone % IDENTITY_TONES.length] ?? IDENTITY_TONES[0];
}
