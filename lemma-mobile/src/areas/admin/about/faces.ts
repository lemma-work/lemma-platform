/** Choosing a Lemma's face. Pure, so `tests/` can hold it.
 *
 *  A character is stored as the variant of the Lemma's own id that draws it
 *  (`lemma-identity:N`, the web's `formatIdentityIcon`), never as the
 *  character's name: the web and the phone both draw a Lemma from its id and
 *  that number, so the face picked is the face every screen draws. */

const IDENTITY_PREFIX = "lemma-identity:";

/** The seed variant `n` of a Lemma draws from (the web's `identityVariantSeed`):
 *  variant zero is the bare id, so a Lemma that never chose a face and one
 *  that chose the first on offer draw the same character. */
export function variantSeed(id: string, variant: number): string {
    return variant === 0 ? id : `${id}#${variant}`;
}

/** The variant of `id` that draws `wanted`, or null when none of the first
 *  800 does (the web's `variantForCharacter`). `characterOf` is the cast's
 *  hash (`ui/face.tsx` `characterForSeed`), passed in so this stays pure. */
export function variantFor(id: string, wanted: string, characterOf: (seed: string) => string): number | null {
    for (let variant = 0; variant < 800; variant += 1) {
        if (characterOf(variantSeed(id, variant)) === wanted) return variant;
    }
    return null;
}

/** What to store for a chosen character. */
export function identityIcon(variant: number): string {
    return IDENTITY_PREFIX + variant;
}

/** "Started 12 October by Sam", "Started 3 March 2025", or "" when unknown. */
export function startedLine(createdAt: string | null, by: string | null, now = new Date()): string {
    if (!createdAt) return "";
    const at = new Date(createdAt);
    if (Number.isNaN(at.getTime())) return "";
    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    const day = `${at.getDate()} ${months[at.getMonth()]}${at.getFullYear() === now.getFullYear() ? "" : ` ${at.getFullYear()}`}`;
    return by ? `Started ${day} by ${by}` : `Started ${day}`;
}
