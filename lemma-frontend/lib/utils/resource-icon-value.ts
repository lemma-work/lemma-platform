/**
 * Is this `icon_url` a picture, or a glyph someone typed?
 *
 * A pod's whole visual identity is one nullable text column, `icon_url`. There
 * is no emoji field on the entity and none can be smuggled through `config` —
 * `PodConfig` is a closed pydantic model, so unknown keys are dropped on parse,
 * silently. The column, the entity and all three API schemas already accept any
 * string, and `IconService.get_managed_storage_path` ignores anything outside
 * the managed icon route, so a bare emoji stored there is inert on the backend:
 * it round-trips, it travels through bundle export/import, and it is never
 * mistaken for a file to garbage-collect.
 *
 * What it is *not* is a URL, and the field name says it is. That lie is the
 * price of shipping this without a migration, and this module is where it is
 * paid: every "is it a glyph?" decision in the product happens here, so the day
 * a real `icon_emoji` field exists, this is the only file that changes.
 */

/**
 * Everything a bare-glyph icon is allowed to be made of. U+200D (zero-width
 * joiner) and U+FE0F (variation selector-16) are written as escapes on
 * purpose: both are invisible, and a character class containing them literally
 * is a class nobody can review.
 */
const GLYPH_ONLY =
    /^[\p{Extended_Pictographic}\p{Emoji_Component}\p{Regional_Indicator}\u200d\ufe0f]+$/u;

/**
 * ...and at least one character that is actually a picture. `Emoji_Component`
 * alone covers plain digits and `#`, so without this "2024" would parse as a
 * glyph. Regional indicators earn their place here too: a flag is made of two
 * of them and contains no pictographic character at all.
 */
const GLYPH_MEANINGFUL = /[\p{Extended_Pictographic}\p{Regional_Indicator}]/u;

/**
 * A ZWJ family (👨‍👩‍👧‍👦) is seven code points before variation selectors, so the
 * cap is well above one emoji and well below a sentence of them.
 */
const MAX_GLYPH_CODE_POINTS = 16;

export type ResourceIconValue =
    | { kind: 'glyph'; glyph: string }
    | { kind: 'url'; url: string }
    | { kind: 'identity'; variant: number };

/**
 * A chosen variant of the generated identity.
 *
 * An empty `icon_url` already means "draw the identity seeded from this
 * resource", which covers everyone who never opens the picker. Choosing a
 * *different* generated face needs somewhere to record which one, and this
 * field is the only somewhere there is — so a variant is stored as a small
 * sentinel rather than as a URL to a rendered file.
 *
 * Storing an index instead of a whole seed keeps the value short and keeps the
 * avatar tied to the resource: the picture is still drawn from the resource's
 * own seed, shifted by the variant. It also degrades honestly — an older client
 * that has never heard of this prefix fails the URL load and falls back, rather
 * than printing the sentinel into the interface.
 */
const IDENTITY_PREFIX = 'lemma-identity:';
const MAX_IDENTITY_VARIANT = 999;

export function formatIdentityIcon(variant: number): string {
    return `${IDENTITY_PREFIX}${variant}`;
}

/** The seed a variant draws from, given the resource's own base seed. */
export function identityVariantSeed(baseSeed: string, variant: number): string {
    return variant === 0 ? baseSeed : `${baseSeed}#${variant}`;
}

/**
 * Anything that is not confidently a glyph is treated as a URL — the behaviour
 * this field has always had. A wrong guess in that direction renders a broken
 * image and falls back; a wrong guess the other way prints a URL into the icon.
 */
export function parseResourceIcon(value?: string | null): ResourceIconValue | null {
    const trimmed = value?.trim();
    if (!trimmed) return null;

    if (trimmed.startsWith(IDENTITY_PREFIX)) {
        // Matched with a digit pattern rather than parsed with `Number`, which
        // reads a bare `lemma-identity:` as 0 and would hand back a perfectly
        // valid-looking variant for a value that carries no variant at all.
        const digits = /^\d{1,3}$/.exec(trimmed.slice(IDENTITY_PREFIX.length));
        const variant = digits ? Number(digits[0]) : NaN;
        if (Number.isInteger(variant) && variant >= 0 && variant <= MAX_IDENTITY_VARIANT) {
            return { kind: 'identity', variant };
        }
        // A malformed sentinel is not a URL either — fall through to the
        // resource's default identity rather than rendering a broken image.
        return null;
    }

    if (
        GLYPH_ONLY.test(trimmed) &&
        GLYPH_MEANINGFUL.test(trimmed) &&
        Array.from(trimmed).length <= MAX_GLYPH_CODE_POINTS
    ) {
        return { kind: 'glyph', glyph: trimmed };
    }

    return { kind: 'url', url: trimmed };
}

/** Convenience for inputs that only want to know whether to keep a value. */
export function isResourceIconGlyph(value?: string | null): boolean {
    return parseResourceIcon(value)?.kind === 'glyph';
}
