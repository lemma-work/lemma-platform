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
    | { kind: 'url'; url: string };

/**
 * Anything that is not confidently a glyph is treated as a URL — the behaviour
 * this field has always had. A wrong guess in that direction renders a broken
 * image and falls back; a wrong guess the other way prints a URL into the icon.
 */
export function parseResourceIcon(value?: string | null): ResourceIconValue | null {
    const trimmed = value?.trim();
    if (!trimmed) return null;

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
