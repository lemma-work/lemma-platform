import type { CSSProperties } from "react";
import styles from "./character.module.css";
import { CHARACTERS, characterForSeed, type CharacterName } from "./cast";
import { identityVariantSeed } from "./resource-icon";

export { CHARACTERS, characterForSeed, type CharacterName };

/** Two sizes on disk. A rail mark crops into a 26px tile and never needs more
 *  than the small one; the profile figure draws the whole sculpture at 280px
 *  and does. Shipping only the large one put megabytes into a sidebar. */
export function characterUrl(name: CharacterName, small = false) {
    return `/teammates/cast/${name}${small ? "-sm" : ""}.webp`;
}

/** The first three characters predate this folder and were stored in
 *  `icon_url` by their old path. Those records still exist. */
const LEGACY: Record<string, CharacterName> = { loop: "loop", pleat: "pleat", frame: "frame" };

export function characterFromUrl(url: string): CharacterName | undefined {
    const cast = /^\/teammates\/cast\/([a-z]+)(-sm)?\.(?:webp|png)$/.exec(url);
    if (cast) return CHARACTERS.find(name => name === cast[1]);
    const legacy = /^\/teammates\/([a-z]+)-v1\.png$/.exec(url);
    return legacy ? LEGACY[legacy[1]] : undefined;
}

/** The variant of `baseSeed` that lands on `wanted`.
 *
 *  The hiring floor shows a candidate drawn from the archetype's seed, then
 *  the pod it creates has an id of its own. Storing the variant that makes
 *  that id draw the same character is what keeps the face you picked the face
 *  you get. Replaces `variantMatching`, which did this by matching the old
 *  creature's tone and body; that one is gone, because unlike `being.tsx` it
 *  had no counterpart on the platform to stay in step with. */
export function variantForCharacter(baseSeed: string, wanted: CharacterName): number | null {
    for (let variant = 0; variant < 800; variant += 1) {
        if (characterForSeed(identityVariantSeed(baseSeed, variant)) === wanted) return variant;
    }
    return null;
}

/** Small marks use an art-directed crop; profiles show the entire sculpture.
 *  Motion responds only to hover/focus. It does not imply a live agent state. */
export function Character({ character, size = 40, label, className }: {
    character: CharacterName; size?: number; label?: string; className?: string;
}) {
    const small = size < 80;
    return <span className={`${styles.character} ${small ? styles.portrait : ""} ${className ?? ""}`}
        data-character={character} role={label ? "img" : undefined} aria-label={label}
        aria-hidden={label ? undefined : true}
        style={{ width: size, height: size, "--character-size": `${size}px` } as CSSProperties}>
        <img src={characterUrl(character, small)} alt="" width={size} height={size} draggable={false} loading="lazy" />
    </span>;
}
