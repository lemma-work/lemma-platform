/**
 * A seeded being, drawn as a standalone SVG string.
 *
 * `ResourceIdentity` already draws this creature, and draws it better: it
 * animates, it carries a state pip, it re-colours itself when the appearance
 * changes. What it cannot do is leave the app. It is a client component whose
 * every fill is `currentColor` or a custom property, so the picture only exists
 * where React mounted it and a stylesheet is in scope.
 *
 * A contact card needs the same face somewhere neither is true — inside a
 * `PHOTO` property in a vCard, rasterised on the edge, opened in an address
 * book. So this renders the same genes to a string with every colour already
 * resolved, and imports the geometry — `FORMS`, `FORM_DEPTH`, `CRESTS` — from
 * the same module the component draws from. Two renderers, one set of shapes:
 * a body that changes in `seeded-identity.ts` changes in both, and the face on
 * someone's phone stays the face in the sidebar.
 *
 * Deliberately **static and stateless**. The component varies eyes and pip by
 * `IdentityState`, which is a live reading of what an agent is doing right now;
 * a saved photo is looked at days later and would be lying by then. Every being
 * here is drawn `idle` — eyes open, no pip — which is what the roster shows at
 * rest anyway.
 */

import {
    CRESTS,
    FORMS,
    FORM_DEPTH,
    identityGenes,
    type IdentityGenes,
} from './seeded-identity';
import {
    IDENTITY_PUPIL,
    IDENTITY_SCLERA,
    IDENTITY_SHADE,
    IDENTITY_SOFTS,
    toneColor,
} from './identity-palette';

/** The component's viewBox, so a crest that rises above y=0 is not clipped off. */
const VIEW_BOX = '-2 -2 104 104';

export interface BeingSvgOptions {
    /** Stable per resource — an id where one exists, a name only as a fallback. */
    seed: string;
    /** Pixel size written onto the root element, for rasterisers that need one. */
    size?: number;
    /**
     * Paint the tone's tinted ground behind the body.
     *
     * On by default, and the default matters more here than in the app. The
     * component draws on transparency because it sits on a surface the product
     * controls; a contact photo is composited by an address book onto a ground
     * nobody told us about, and a violet body on an unknown one is a coin flip.
     */
    background?: boolean;
}

/** Trim float noise — `8.960000000000001` is three bytes of vCard for nothing. */
function round(value: number): number {
    return Math.round(value * 1000) / 1000;
}

/**
 * Resolve the two names the shared geometry leaves to the cascade.
 *
 * The strings in `seeded-identity.ts` are written for a stylesheet: crests
 * stroke themselves in `currentColor` and the body rim reads
 * `var(--identity-shade)`. Both are dead outside the app, and an unresolved
 * paint does not fail loudly — it renders black, or renders nothing.
 */
function resolvePaints(markup: string, tone: string): string {
    return markup
        .replaceAll('currentColor', tone)
        .replaceAll('var(--identity-shade)', IDENTITY_SHADE);
}

function eyes(genes: IdentityGenes): string {
    const radius = genes.eyeR;
    const centres = [50 - genes.eyeSpacing, 50 + genes.eyeSpacing];
    const sclera = centres
        .map(
            (cx) =>
                `<ellipse cx="${cx}" cy="${genes.eyeY}" rx="${radius}" ry="${round(radius * 1.12)}" fill="${IDENTITY_SCLERA}"/>`,
        )
        .join('');
    const pupils = centres
        .map(
            (cx) =>
                `<circle cx="${cx}" cy="${genes.eyeY}" r="${round(radius * 0.46)}" fill="${IDENTITY_PUPIL}"/>`,
        )
        .join('');
    return sclera + pupils;
}

/**
 * The being for a seed, as SVG markup.
 *
 * The layer order is the component's, and the order is load-bearing: the crest
 * is drawn *under* the body so it reads as rising out of a solid crown rather
 * than as a sticker laid on top, and the depth overlay is clipped to the body
 * so a blunt half-plane can shade a curved silhouette without spilling past it.
 */
export function renderBeingSvg({ seed, size, background = true }: BeingSvgOptions): string {
    const genes = identityGenes(seed);
    const tone = toneColor(genes.tone);
    const form = FORMS[genes.form] ?? FORMS[0];
    // A clip path is referenced by id, and two of these can end up in one
    // document — an OG card drawing a roster, say. Seeded from the genes rather
    // than a counter so the markup stays a pure function of the seed.
    const clipId = `lm-being-${genes.tone}${genes.form}${genes.crest}${genes.eyeSpacing}${genes.eyeY}`;
    const dimensions = size ? ` width="${size}" height="${size}"` : '';

    return [
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${VIEW_BOX}"${dimensions}>`,
        `<defs><clipPath id="${clipId}"><path d="${form}"/></clipPath></defs>`,
        background
            ? `<rect x="-2" y="-2" width="104" height="104" fill="${IDENTITY_SOFTS[genes.tone] ?? IDENTITY_SOFTS[0]}"/>`
            : '',
        `<g fill="${tone}">${resolvePaints(CRESTS[genes.crest] ?? '', tone)}</g>`,
        `<path d="${form}" fill="${tone}"/>`,
        `<g clip-path="url(#${clipId})">${FORM_DEPTH[genes.form] ?? ''}</g>`,
        `<path d="${form}" fill="none" stroke="${IDENTITY_SHADE}" stroke-opacity=".14" stroke-width="1.5"/>`,
        eyes(genes),
        '</svg>',
    ].join('');
}

/**
 * The same being as a `data:` URI.
 *
 * Base64 rather than percent-encoded: the markup carries `#` in every fill, and
 * a raw `#` inside a data URI starts a fragment — the picture silently truncates
 * at the first colour.
 */
export function beingDataUri(options: BeingSvgOptions): string {
    const svg = renderBeingSvg(options);
    // The markup is ASCII by construction — path data, hex colours, and an id
    // built from integers — so `btoa` is safe and works on the edge, where
    // `Buffer` does not exist.
    return `data:image/svg+xml;base64,${btoa(svg)}`;
}
