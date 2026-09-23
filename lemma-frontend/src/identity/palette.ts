/** Where the values above are published for the product. Read by the test. */
export const IDENTITY_STYLESHEET = 'styles/features/resource-identity.css';

export const IDENTITY_TONES: readonly string[] = [
    '#e04a1c',
    '#6f7a18',
    '#148554',
    '#2350e8',
    '#8a3a9c',
];

/** The tinted grounds a mark sits on, indexed the same way. */
export const IDENTITY_SOFTS: readonly string[] = [
    '#fce8e0',
    '#eef0da',
    '#ddf2e7',
    '#e2e8fd',
    '#f2e4f6',
];

export const IDENTITY_SCLERA = '#fdfcf8';
export const IDENTITY_PUPIL = '#2b2924';
/** Darkens whatever tone sits under it, so one value serves all five. */
export const IDENTITY_SHADE = '#000000';

/** The tone a set of genes draws in, wrapped so an out-of-range index cannot blank a face. */
export function toneColor(tone: number): string {
    return IDENTITY_TONES[tone % IDENTITY_TONES.length] ?? IDENTITY_TONES[0];
}

/**
 * Which press ink a creature's tone is printed against.
 *
 * Both runs now share one hue order — red, olive, forest, klein, aubergine —
 * so the pairing stops being a hand-fitted table and becomes a rule: shift by
 * two. That is a derangement on five items, so no creature can ever land on
 * its own hue, and the closest any pair gets is 135° apart.
 *
 *   red 11°       → forest 154°    (143° apart)
 *   olive 66°     → klein 229°     (163°)
 *   forest 154°   → aubergine 289° (135°)
 *   klein 229°    → red 11°        (142°)
 *   aubergine 289° → olive 66°     (137°)
 *
 * Separation is not the whole fix — a creature also sits on a paper disc
 * wherever it meets a field — but it is what keeps a card from being one hue
 * in two values.
 */
const FIELD_SHIFT = 2;

/**
 * Where a teammate's field comes from.
 *
 * Returned as `var()` references rather than hex so the press stays one
 * definition. It does **not** vary with the theme: a teammate's colour is
 * their identity, and identity that repaints when somebody changes the
 * appearance is not identity.
 */
export function pressSlot(tone: number): { field: string; ink: string } {
    const index = ((tone % TONE_SLOTS) + TONE_SLOTS) % TONE_SLOTS;
    return { field: `var(--press-${(index + FIELD_SHIFT) % TONE_SLOTS})`, ink: "var(--press-ink)" };
}

/** How many inks the press holds. Matches `--press-0..4`. */
export const TONE_SLOTS = 5;
