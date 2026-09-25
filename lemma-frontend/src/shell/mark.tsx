import { memo, useEffect, useState } from "react";
import { parseResourceIcon, identityVariantSeed } from "./resource-icon";
import { Character, characterFromUrl, characterForSeed } from "./character";
import { CharacterPuppet } from "./character-puppet";
import type { CharacterName } from "./cast";

/** A teammate's mark.
 *
 *  Three contents, in order of how much somebody meant them: the picture they
 *  uploaded, the emoji they typed, and — for the great majority who have done
 *  neither — a character derived from the pod's id. Not initials, which are a
 *  label rather than a face: `LD`, `LM` and `LC` in one rail is three
 *  teammates you cannot tell apart at a glance, which is the whole job of the
 *  thing.
 *
 *  The character is not decoration for the empty case. It is the default face,
 *  and picking one in the hiring modal only shifts which member of the cast
 *  the pod's own seed lands on.
 *
 *  A cast rather than a procedural creature, and the trade is deliberate. A
 *  generator draws a distinct face for any id, which twenty-four characters
 *  cannot: eleven teammates in a rail have roughly a 93% chance of a repeat.
 *  What the cast buys instead is fidelity — these are the same sculptures the
 *  profile and the character studies draw, where a flat generated SVG sitting
 *  next to them reads as a placeholder. If per-pod uniqueness is wanted back,
 *  it wants a seeded variation axis on top of the shape rather than two
 *  visual languages at once.
 *
 *  Marks draw the live rig, not a picture of it. A still with a hover tilt is
 *  what the cast looked like before the rigs existed, and next to a character
 *  study that breathes it reads as a sticker. Below `ANIMATED_MIN` there is
 *  nothing to see — a wave is four pixels — so those stay stills. */

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

/** Kept for the call bar, which paints a halo from the same seed. */
export function markTint(seed: string): { background: string; color: string } {
    const hue = hueOf(seed);
    return {
        background: `hsl(${hue} var(--mark-sat) var(--mark-bg))`,
        color: `hsl(${hue} var(--mark-sat) var(--mark-fg))`,
    };
}

export const Mark = memo(function Mark({
    seed,
    name,
    icon,
    size = 26,
    className,
    greeting,
    still = false,
}: {
    seed: string;
    name: string;
    icon?: string | null;
    size?: number;
    className?: string;
    /** Bump to make the character wave. Ignored by pictures and emoji, which
     *  have nothing to wave with. */
    greeting?: number;
    /** Draw the picture, not the rig, whatever the size. For a list that
     *  would otherwise run a rig per row — see `Rail`. */
    still?: boolean;
}) {
    const [broken, setBroken] = useState(false);
    useEffect(() => setBroken(false), [icon]);
    const parsed = parseResourceIcon(icon);
    const tint = markTint(seed);
    const classes = "mark" + (className ? " " + className : "");
    const box = { width: size, height: size, borderRadius: "var(--r-mark)" };

    const character = parsed?.kind === "url" ? characterFromUrl(parsed.url) : undefined;
    if (character) return draw(character, size, name, className, greeting, still);

    if (parsed?.kind === "url" && !broken) {
        return (
            <img
                className={classes + " mark--image"}
                style={box}
                src={parsed.url}
                alt=""
                width={size}
                height={size}
                /* Right for every mark, not only the ones pointing off-site.
                   The host serving the picture has no business knowing which
                   page of this app asked for it, and for the site favicons the
                   tool cards draw that is the difference between a request
                   that says "somebody wanted your icon" and one that names the
                   transcript it was wanted from. */
                referrerPolicy="no-referrer"
                /* A long transcript can carry dozens of these. Only the ones
                   scrolled into view are worth a request. */
                loading="lazy"
                onError={() => setBroken(true)}
            />
        );
    }

    if (parsed?.kind === "glyph") {
        return (
            <span
                className={classes}
                style={{
                    ...box,
                    background: tint.background,
                    color: tint.color,
                    fontSize: Math.round(size * 0.56),
                }}
                aria-hidden="true"
            >
                {parsed.glyph}
            </span>
        );
    }

    /* No picture and no emoji — or a stored variant, which is a choice about
       *which* character rather than whether to have one. */
    return draw(
        characterForSeed(identityVariantSeed(seed, parsed?.kind === "identity" ? parsed.variant : 0)),
        size, name, className, greeting, still,
    );
});

/** Small enough that a gesture is a few pixels of noise. */
const ANIMATED_MIN = 30;

function draw(character: CharacterName, size: number, name: string, className?: string, greeting?: number, still = false) {
    /* The caller's class reaches both shapes, and it has to. A mark big enough
       to animate becomes a puppet; drop the class on that branch and a pod with
       a character for an icon produces a mark nothing can select, which is how
       a rule like `.head > .mark { display:none }` silently stops hiding
       anything. */
    return size >= ANIMATED_MIN && !still
        ? <CharacterPuppet character={character} size={size} label={name} className={className} greeting={greeting} />
        : <Character character={character} size={size} label={name} className={className} />;
}
