import { identityGenes } from "@/identity/seeded-identity";

/** Everything a bare-glyph icon may be made of. The zero-width joiner and
 *  variation selector are escaped on purpose: both are invisible, and a class
 *  containing them literally is a class nobody can review. */
const GLYPH_ONLY = /^[\p{Extended_Pictographic}\p{Emoji_Component}\p{Regional_Indicator}‍️]+$/u;

/** …and at least one character that is actually a picture. `Emoji_Component`
 *  alone covers digits, so without this "2024" would parse as a glyph. */
const GLYPH_MEANINGFUL = /[\p{Extended_Pictographic}\p{Regional_Indicator}]/u;

/** A ZWJ family is seven code points before variation selectors. */
const MAX_GLYPH_CODE_POINTS = 16;

const IDENTITY_PREFIX = "lemma-identity:";

export type ResourceIcon =
    | { kind: "glyph"; glyph: string }
    | { kind: "url"; url: string }
    | { kind: "identity"; variant: number };

const MAX_IDENTITY_VARIANT = 999;

/** What goes in `icon_url` when somebody picks a generated face. */
export function formatIdentityIcon(variant: number): string {
    return IDENTITY_PREFIX + variant;
}

/** The seed a variant draws from, given the resource's own base seed.
 *
 *  Variant zero is the bare seed, so a pod that never chose a face and a pod
 *  that chose the first one on offer draw the same creature — which is what
 *  makes "pick one" feel like picking rather than like switching something on. */
export function identityVariantSeed(baseSeed: string, variant: number): string {
    return variant === 0 ? baseSeed : baseSeed + "#" + variant;
}

export function parseResourceIcon(value?: string | null): ResourceIcon | null {
    const trimmed = value?.trim();
    if (!trimmed) return null;

    /* The generated-identity sentinel is not a URL either — a malformed one
       falls through to the default face rather than a broken image.

       Matched with a digit pattern rather than parsed with `Number`, which
       reads a bare `lemma-identity:` as 0 and would hand back a
       perfectly valid-looking variant for a value carrying no variant. */
    if (trimmed.startsWith(IDENTITY_PREFIX)) {
        const digits = /^\d{1,3}$/.exec(trimmed.slice(IDENTITY_PREFIX.length));
        const variant = digits ? Number(digits[0]) : NaN;
        if (Number.isInteger(variant) && variant >= 0 && variant <= MAX_IDENTITY_VARIANT) {
            return { kind: "identity", variant };
        }
        return null;
    }

    if (
        GLYPH_ONLY.test(trimmed) &&
        GLYPH_MEANINGFUL.test(trimmed) &&
        Array.from(trimmed).length <= MAX_GLYPH_CODE_POINTS
    ) {
        return { kind: "glyph", glyph: trimmed };
    }

    /* Anything not confidently a glyph is treated as a URL, which is the
       behaviour the field has always had: a wrong guess that way renders a
       broken image and falls back, the other way prints a URL on screen. */
    return { kind: "url", url: trimmed };
}

export function distinctIdentityVariants(baseSeed: string, count: number): number[] {
    const chosen: number[] = [];
    const seen = new Set<string>();
    for (let variant = 0; variant < 800 && chosen.length < count; variant += 1) {
        const genes = identityGenes(identityVariantSeed(baseSeed, variant));
        const key = genes.tone + "|" + genes.form;
        if (seen.has(key)) continue;
        seen.add(key);
        chosen.push(variant);
    }
    return chosen;
}
