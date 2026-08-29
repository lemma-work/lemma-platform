/**
 * A visual identity for every resource, derived rather than stored.
 *
 * A pod's agents used to be nine PNGs of one orange mascot, so nine agents drew
 * nine identical asterisks; every table drew the same table glyph as every other
 * table. Only four entities (`pods`, `agents`, `functions`, `workflow_flows`)
 * even have an `icon_url` column to hang a picture on, which means an identity
 * system that needs storage cannot cover tables, documents, apps, schedules or
 * connectors at all.
 *
 * So this derives the identity from the seed instead. Nothing is stored,
 * nothing is uploaded, and the six resource types with nowhere to put an icon
 * are covered on the same day as the four that have one. An explicit choice —
 * an uploaded picture, a typed emoji — still wins; see `ResourceIcon`.
 *
 * Two renderings share one generator, on one rule: **agency is saturated,
 * inert is tinted.** A `being` is an agent, drawn as a solid body with eyes. A
 * `mark` is a table or a function, drawn as the type's glyph on a tinted
 * ground — because you scan a list for "the table about invoices", so a table
 * has to keep reading as a table.
 */

/** How many distinct identities the parameter space holds. */
export const TONE_COUNT = 5;
export const FORM_COUNT = 8;
export const CREST_COUNT = 4;

/**
 * Bodies past `FORM_COUNT` that the generator never draws.
 *
 * Reserved rather than carved out of the seeded range on purpose: taking index
 * 7 back would have cost the roster twenty buckets (160 → 140) to identify one
 * creature, which is a bad trade in a system whose whole problem is collisions.
 * Appending costs nothing — the generator still rolls 0–7 and every agent's face
 * is bit-for-bit what it was before this existed.
 */
export const RESERVED_FORM_COUNT = 1;
export const LEM_FORM = FORM_COUNT;

/**
 * 160 visually-distinct buckets at sidebar size, where the eyes are too small
 * to separate anything and only tone, silhouette and crest survive.
 *
 * The count is `TONE_COUNT * FORM_COUNT * CREST_COUNT`, and the sizing of each
 * factor was measured rather than guessed. Tones are capped at five because
 * `design.md` sanctions exactly five identity colours and a generative system
 * that invents hues would undo the palette it lives in. Forms carry the slack
 * instead: geometry is not on the palette, so widening it from five shapes to
 * eight is free, and it buys more separation than the two extra colours would
 * have (160 buckets against 140). At a thirty-agent pod that is the difference
 * between roughly a quarter of the roster having a twin and roughly a sixth.
 *
 * The ceiling is real and worth stating: past about sixty agents in one pod,
 * most of the roster shares a bucket with somebody and the name beside the
 * avatar is doing the work. This is an identity system for a pod, not for a
 * directory of thousands.
 */
export const IDENTITY_BUCKETS = TONE_COUNT * FORM_COUNT * CREST_COUNT;

export type IdentityState =
    | 'idle'
    | 'thinking'
    | 'running'
    | 'waiting'
    | 'failed'
    | 'asleep';

export interface IdentityGenes {
    tone: number;
    form: number;
    crest: number;
    /** Half the distance between the eyes, in viewBox units. */
    eyeSpacing: number;
    eyeY: number;
    eyeR: number;
    ground: number;
    groundRotation: number;
    /**
     * Which of `MOTION_PHASES` this creature's animations start on.
     *
     * Without it every avatar on a page starts its cycle at load and the whole
     * roster blinks in lockstep — which does not read as a room full of agents,
     * it reads as the page glitching. Drawn last so that adding it left tone,
     * form and crest on the seeds they already had.
     */
    phase: number;
}

/** Negative delays, so a freshly-mounted avatar starts mid-cycle rather than waiting. */
export const MOTION_PHASES = 8;

/**
 * FNV-1a. Chosen because it is stable across runtimes and has no dependencies —
 * the same agent must draw the same avatar on the server, in the browser, and
 * in a test, or hydration flickers a different creature into place.
 */
export function hashSeed(value: string): number {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
}

/** mulberry32 — small, fast, and good enough for six draws per resource. */
function makeRandom(seed: number): () => number {
    let state = seed;
    return () => {
        state = (state + 0x6d2b79f5) | 0;
        let t = Math.imul(state ^ (state >>> 15), 1 | state);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/**
 * The seed is the resource's identity, not its name, so renaming an agent must
 * not hand it a different face. Callers pass the id when they have one and fall
 * back to the name only when they do not.
 */
export function identitySeed(...parts: Array<string | null | undefined>): string {
    return parts.filter(Boolean).join('/');
}

/**
 * The one seed an agent is drawn from, wherever it is drawn.
 *
 * The rule above — id when there is one, name only as a fallback — was left to
 * each call site to remember, and the sidebar forgot: it seeded its rows on
 * `agent.name` while the agent's own header seeded on `agent.id`, so the same
 * agent wore two different faces on two halves of one screen. Nothing catches
 * that, because both faces are valid output for the seed each was handed.
 *
 * So the rule lives here now, and a caller passes the agent rather than a
 * string. The fallback is still worth keeping: an agent being composed in the
 * "new agent" flow has a name before it has an id, and a face that appears only
 * after the first save would be worse than one that settles on it.
 */
export function agentIdentitySeed(agent: {
    id?: string | null;
    name?: string | null;
}): string {
    return agent.id?.trim() || agent.name?.trim() || '';
}

/**
 * The pod's default responder is the same creature in every pod, so its genes
 * are written down rather than rolled.
 *
 * A seeded face would have been wrong twice over: it would introduce a stranger
 * on the one row that is meant to be the responder you already know, and it
 * would be a different stranger in every pod. The previous answer — the Lemma
 * trademark on a tinted tile — was wrong differently: it drew the pod's most
 * capable agent in the treatment reserved for *inert* things, so the being with
 * the most agency in the system was the one being that could not open its eyes,
 * carry a state pip, or move. `design.md` had to write a special rule for the
 * recents list to work around it.
 *
 * The values sit mid-range on every axis the generator varies, so Lem stands in
 * a lineup as one of the cast rather than as an outsized mascot. Its body is the
 * one thing no agent can borrow.
 */
export const LEM_SEED = '__lem__';

export const LEM_GENES: IdentityGenes = {
    tone: 0,
    form: LEM_FORM,
    // No crest. The reserved body has a concave waist, and a crest anchors to
    // the top-centre expecting a solid convex crown to rise out of.
    crest: 0,
    eyeSpacing: 14,
    // Higher than the seeded 48–55, because the plinth carries its mass up top:
    // eyes at 51 would sit on the waist rather than in the face.
    eyeY: 46,
    eyeR: 9,
    ground: 0,
    groundRotation: 0,
    phase: 0,
};

export function identityGenes(seed: string): IdentityGenes {
    // One reserved seed, checked before the hash. Everything downstream — the
    // sidebar row, the front door, the transcript avatar — draws Lem by passing
    // this seed to the same `being` renderer every agent uses, so there is no
    // second code path to keep in step with the first.
    if (seed === LEM_SEED) return LEM_GENES;

    const random = makeRandom(hashSeed(seed));
    return {
        tone: Math.floor(random() * TONE_COUNT),
        form: Math.floor(random() * FORM_COUNT),
        crest: Math.floor(random() * CREST_COUNT),
        eyeSpacing: 12 + Math.floor(random() * 6),
        eyeY: 48 + Math.floor(random() * 8),
        eyeR: 8 + Math.floor(random() * 3),
        ground: Math.floor(random() * GROUNDS.length),
        groundRotation: [0, 90, 180, 270][Math.floor(random() * 4)],
        phase: Math.floor(random() * MOTION_PHASES),
    };
}

/**
 * Body silhouettes, drawn in a 100×100 box. Silhouette is what survives the
 * shrink to a 16px sidebar row — colour blurs together at that size and the
 * eyes are two dots, so the outline is carrying the recognition.
 */
export const FORMS: readonly string[] = [
    'M8 41a27 27 0 0 1 27-27h30a27 27 0 0 1 27 27v26a27 27 0 0 1-27 27H35A27 27 0 0 1 8 67Z',
    'M50 12a42 42 0 1 1 0 84 42 42 0 0 1 0-84Z',
    'M50 12 88 33v42L50 96 12 75V33Z',
    'M8 56a42 42 0 0 1 84 0v30a8 8 0 0 1-8 8H16a8 8 0 0 1-8-8Z',
    'M30 14h44a18 18 0 0 1 18 18v28a34 34 0 0 1-34 34H26a18 18 0 0 1-18-18V48A34 34 0 0 1 30 14Z',
    'M50 10a34 34 0 0 1 34 34v18a34 34 0 0 1-68 0V44A34 34 0 0 1 50 10Z',
    'M14 22a7 7 0 0 1 7-7h58a7 7 0 0 1 7 7v32a36 36 0 0 1-72 0Z',
    'M50 14.4 89.6 54 50 93.6 10.4 54Z',
    /*
     * Index 8 — reserved for Lem, never rolled. See `RESERVED_FORM_COUNT`.
     *
     * All eight seeded bodies are convex; this one is not. That is the entire
     * distinction, and it is deliberately the only one: Lem keeps the cast's
     * tone, its light, its eyes and its pip, so it reads as one of them, and it
     * is told apart by the single channel that survives the shrink to a 20px
     * transcript avatar. Colour cannot do that job — an agent may roll tone 0
     * too — and a crest cannot, because the visible band above a body is only
     * about eight units tall and detail dies there first.
     *
     * Concavity is also the one property the generator can never reach by
     * accident, so this is a guarantee rather than a low probability: adding a
     * ninth convex blob would have left Lem one unlucky hash away from a twin.
     */
    'M50 8C70 8 86 24 86 44C86 58 70 62 70 72H80A8 8 0 0 1 88 80V86A8 8 0 0 1 80 94H20A8 8 0 0 1 12 86V80A8 8 0 0 1 20 72H30C30 62 14 58 14 44C14 24 30 8 50 8Z',
];

/**
 * Faint plane shading, so a form reads as a solid rather than a flat sticker.
 *
 * Each entry is drawn clipped to its own body, which is why these can be blunt
 * half-planes instead of paths traced along the silhouette. The overlays are
 * white and black at low alpha rather than named colours: they modulate
 * whatever tone the body already has, so one set of numbers works for all five
 * tones in both appearances instead of ten hand-tuned pairs.
 *
 * One light, from the upper left, across all eight. That constraint is the
 * whole reason these are worth having: a set where the squircle is lit from
 * directly above and the sphere from the upper left does not read as eight
 * creatures under one sun, it reads as eight unrelated stickers.
 *
 * The faceted forms — the hexagon as an isometric cube, the rotated square as a
 * folded edge — take hard-edged planes, because a crease *is* the shape there
 * and the seam is the point. The round forms take oversized offset circles
 * instead: clipped to a body, a big circle's edge crosses it as a gentle
 * terminator, where a half-plane would lay a hard band across a curved surface
 * and read as a stripe painted on rather than as light falling.
 */
export const FORM_DEPTH: readonly string[] = [
    '<circle cx="24" cy="22" r="48" fill="#fff" opacity=".11"/><circle cx="80" cy="88" r="50" fill="#000" opacity=".12"/>',
    '<circle cx="30" cy="30" r="40" fill="#fff" opacity=".12"/><circle cx="74" cy="82" r="44" fill="#000" opacity=".12"/>',
    '<path d="M50 12 88 33 50 54 12 33Z" fill="#fff" opacity=".17"/><path d="M12 33 50 54v42L12 75Z" fill="#fff" opacity=".05"/><path d="M88 33v42L50 96V54Z" fill="#000" opacity=".15"/><path d="M50 54 12 33M50 54 88 33M50 54v42" stroke="#000" stroke-opacity=".13" stroke-width="1.4" fill="none"/>',
    '<circle cx="26" cy="26" r="46" fill="#fff" opacity=".12"/><circle cx="80" cy="90" r="48" fill="#000" opacity=".12"/>',
    '<path d="M0 0h100L0 100Z" fill="#fff" opacity=".09"/><path d="M100 0v100H0Z" fill="#000" opacity=".10"/>',
    '<rect x="12" width="22" height="100" fill="#fff" opacity=".13"/><rect x="64" width="26" height="100" fill="#000" opacity=".13"/>',
    '<circle cx="26" cy="20" r="44" fill="#fff" opacity=".12"/><circle cx="80" cy="82" r="48" fill="#000" opacity=".12"/>',
    '<path d="M50 14.4 89.6 54 50 93.6Z" fill="#000" opacity=".12"/><path d="M50 14.4 10.4 54 50 93.6Z" fill="#fff" opacity=".11"/><path d="M50 14.4v79.2" stroke="#000" stroke-opacity=".12" stroke-width="1.4" fill="none"/>',
    /* Lem. A round body takes the oversized offset circles, for the same reason
       the other round forms do — a half-plane would lay a hard stripe across the
       curve. Same sun, upper left, as all eight. */
    '<circle cx="26" cy="26" r="46" fill="#fff" opacity=".12"/><circle cx="80" cy="88" r="46" fill="#000" opacity=".12"/>',
];

/**
 * Crests exist to vary the silhouette, which is the only channel that still
 * separates two avatars at 16px.
 *
 * They all rise from the centre-top and start at y=27, below the surface of
 * every one of the eight forms — an earlier set placed ears out at x=20 and a
 * tab at the top-right corner, which straddled the first five silhouettes
 * correctly and then floated free of the capsule and the rotated square when
 * those were added. Anchoring to the one region every form is guaranteed solid
 * means a crest can never detach from the body that wears it.
 */
export const CRESTS: readonly string[] = [
    '',
    '<path d="M50 27V6" stroke="currentColor" stroke-width="5" stroke-linecap="round" fill="none"/><circle cx="50" cy="4" r="5"/>',
    '<path d="M40 27V7M60 27V7" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" fill="none"/><circle cx="40" cy="5" r="4"/><circle cx="60" cy="5" r="4"/>',
    '<path d="M50 27V10" stroke="currentColor" stroke-width="5" stroke-linecap="round" fill="none"/><rect x="34" y="4" width="32" height="9" rx="4.5"/>',
];

/**
 * Miniature interfaces, for the one resource that *is* a screen.
 *
 * An app card had no picture of the app on it — a monogram in a box, a title,
 * two lines of description and a grey footer, in a tile tall enough to hold
 * something. There is no screenshot field to draw from and no capture pipeline
 * to build one, so this stands in: a seeded abstraction of a layout — a header
 * bar and some content blocks — which says "this is a page" and gives every app
 * in the grid a different silhouette. If real thumbnails ever land, they drop
 * into the same slot and this becomes the placeholder for the ones still
 * building.
 *
 * Drawn in `currentColor` at varying opacity rather than in separate fills, so
 * a screen is one hue in several values by construction — the same rule the pod
 * marks were rebuilt around after two hues at full strength turned out to clash
 * with each other in every combination the palette could produce.
 */
export const APP_SCREENS: readonly string[] = [
    '<rect x="8" y="22" width="34" height="60" rx="3" opacity=".38"/><rect x="48" y="22" width="104" height="10" rx="2.5" opacity=".62"/><rect x="48" y="38" width="48" height="44" rx="2.5" opacity=".26"/><rect x="102" y="38" width="50" height="20" rx="2.5" opacity=".26"/><rect x="102" y="62" width="50" height="20" rx="2.5" opacity=".5"/>',
    '<rect x="8" y="22" width="70" height="9" rx="2.5" opacity=".6"/><rect x="8" y="38" width="44" height="44" rx="3" opacity=".26"/><rect x="58" y="38" width="44" height="44" rx="3" opacity=".5"/><rect x="108" y="38" width="44" height="44" rx="3" opacity=".26"/>',
    '<rect x="8" y="22" width="60" height="9" rx="2.5" opacity=".6"/><rect x="8" y="38" width="94" height="44" rx="3" opacity=".45"/><rect x="108" y="38" width="44" height="20" rx="3" opacity=".26"/><rect x="108" y="62" width="44" height="20" rx="3" opacity=".26"/>',
    '<rect x="8" y="22" width="52" height="9" rx="2.5" opacity=".6"/><rect x="8" y="38" width="144" height="10" rx="2.5" opacity=".26"/><rect x="8" y="52" width="144" height="10" rx="2.5" opacity=".44"/><rect x="8" y="66" width="144" height="10" rx="2.5" opacity=".26"/>',
    '<rect x="8" y="22" width="46" height="9" rx="2.5" opacity=".6"/><rect x="8" y="60" width="20" height="22" rx="2" opacity=".3"/><rect x="34" y="48" width="20" height="34" rx="2" opacity=".45"/><rect x="60" y="36" width="20" height="46" rx="2" opacity=".6"/><rect x="86" y="54" width="20" height="28" rx="2" opacity=".36"/><rect x="112" y="30" width="20" height="52" rx="2" opacity=".72"/>',
];

/** Seeded grounds for marks. Quiet enough to sit behind a type glyph. */
export const GROUNDS: readonly string[] = [
    '<circle cx="2" cy="98" r="60"/>',
    '<path d="M0 100 100 0v42L42 100Z"/>',
    '<rect y="60" width="100" height="40"/>',
    '<g opacity=".62"><circle cx="20" cy="20" r="4.5"/><circle cx="50" cy="20" r="4.5"/><circle cx="80" cy="20" r="4.5"/><circle cx="20" cy="50" r="4.5"/><circle cx="50" cy="50" r="4.5"/><circle cx="80" cy="50" r="4.5"/><circle cx="20" cy="80" r="4.5"/><circle cx="50" cy="80" r="4.5"/><circle cx="80" cy="80" r="4.5"/></g>',
    '<g fill="none" stroke="currentColor" stroke-width="9"><circle cx="100" cy="2" r="36"/><circle cx="100" cy="2" r="62"/><circle cx="100" cy="2" r="88"/></g>',
];

export interface PodField {
    form: number;
    /** A value of the pod's single hue, never a second hue. See `podFields`. */
    value: 'light' | 'solid';
    cx: number;
    cy: number;
    scale: number;
}

/**
 * Where the two fields sit. Varying the arrangement buys back the distinctness
 * that dropping the second hue costs, and costs nothing in harmony.
 */
const POD_ARRANGEMENTS: ReadonlyArray<ReadonlyArray<{ cx: number; cy: number }>> = [
    [{ cx: 36, cy: 46 }, { cx: 64, cy: 56 }],
    [{ cx: 38, cy: 58 }, { cx: 63, cy: 43 }],
    [{ cx: 45, cy: 37 }, { cx: 57, cy: 63 }],
    [{ cx: 34, cy: 51 }, { cx: 63, cy: 50 }],
];

/**
 * A pod is a team, so its mark is one.
 *
 * Not a tinted tile with a glyph in it — a pod is not an inert thing you open,
 * it is the boundary a group of people and agents share, and the icon should
 * say so before you read the name. Three figures, overlapping, one of them
 * unmistakably a person: the shape difference between the human and the others
 * is what encodes "agents and people" without a label.
 *
 * Returned back-to-front, so a caller can paint them in array order and get the
 * overlap right.
 */
/**
 * A pod, drawn as two overlapping fields rather than a crowd of little people.
 *
 * The first attempt put three tiny figures — one of them a person silhouette —
 * inside the tile. It looked fine at 72px and was mud at 20px, which is the
 * size a pod is *actually* seen at in the sidebar. The lesson is not "use two
 * figures instead of three"; it is that small detail cannot live in a small
 * square at all. Anything with a face, a limb or a knockout outline is gone
 * below about 32px, and the pod switcher never gets that much room.
 *
 * So the plurality is carried by *fields* instead: two large bodies drawn from
 * the same vocabulary the agents use, overlapping by about half, with the
 * intersection darkened. Nothing in it is smaller than a third of the tile, so
 * it survives the shrink intact — and at every size it reads as two things
 * sharing a boundary, which is what a pod is. Reusing `FORMS` is what keeps a
 * pod and the agents inside it looking like one system rather than two.
 *
 * **One hue per pod, in three values.** The first version gave each field its
 * own tone, picked two steps around the palette, and it was indefensible: the
 * five tones are discrete jewel colours, not a hue wheel, so "two steps along"
 * paired rust with violet and terracotta with forest green — clashing pairs at
 * full saturation, overlapping, on a third hue's tinted ground. Three unrelated
 * hues inside a 24px square is a mess no arrangement can rescue.
 *
 * Now the ground, the light field and the solid field are all the same hue at
 * increasing strength, and the overlap is darker still. A monochrome ramp
 * cannot clash with itself, so every pod is harmonious by construction, and the
 * variety that the second hue used to provide comes from the arrangement and
 * the form pair instead — which cost nothing to harmony.
 */
export function podFields(seed: string): PodField[] {
    const genes = identityGenes(seed);
    const arrangement = POD_ARRANGEMENTS[genes.crest % POD_ARRANGEMENTS.length];
    return [
        { form: genes.form, value: 'light', ...arrangement[0], scale: 0.68 },
        { form: (genes.form + 3) % FORM_COUNT, value: 'solid', ...arrangement[1], scale: 0.68 },
    ];
}


export interface EyeShape {
    kind: 'open' | 'wide' | 'closed' | 'line';
    pupilX: number;
    pupilY: number;
    /** Fraction of the eye covered by a lid, 0–1. */
    lid: number;
}

export interface StateLook {
    eye: EyeShape;
    /** A CSS custom-property reference, or null when the state needs no pip. */
    pip: string | null;
    opacity: number;
}

/**
 * State lives in the eyes and a status pip; it never touches the body's colour.
 *
 * That separation is the point. A failed agent keeps its own hue, so you do not
 * lose track of *which* agent broke at the moment it breaks — and because the
 * eye shape changes alongside the pip, state is never carried by colour alone.
 */
export const STATE_LOOKS: Record<IdentityState, StateLook> = {
    idle: { eye: { kind: 'open', pupilX: 0, pupilY: 0, lid: 0 }, pip: null, opacity: 1 },
    thinking: { eye: { kind: 'open', pupilX: -0.34, pupilY: -0.36, lid: 0.34 }, pip: 'var(--state-info)', opacity: 1 },
    running: { eye: { kind: 'open', pupilX: 0, pupilY: 0, lid: 0 }, pip: 'var(--state-success)', opacity: 1 },
    waiting: { eye: { kind: 'wide', pupilX: 0, pupilY: 0.2, lid: 0 }, pip: 'var(--state-warning)', opacity: 1 },
    failed: { eye: { kind: 'closed', pupilX: 0, pupilY: 0, lid: 0 }, pip: 'var(--state-error)', opacity: 1 },
    asleep: { eye: { kind: 'line', pupilX: 0, pupilY: 0, lid: 0 }, pip: null, opacity: 0.4 },
};

/**
 * Below this, eye-level motion is a shimmer rather than an expression, and a
 * list of forty of them is a distraction. Body-level motion still runs, so a
 * running agent breathes even in a 20px sidebar row.
 */
export const RICH_MOTION_MIN_SIZE = 32;

/** Below this, the pip is bigger than the feature it sits beside. */
export const PIP_MIN_SIZE = 20;
