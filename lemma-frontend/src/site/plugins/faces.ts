import { BUILT, PLANNED } from "./catalog";

/** The clay characters in `public/teammates/cast`: every Lemma wears one, so
 *  every plugin, which starts a Lemma off, wears one too, small, as the mark
 *  that tells it apart. */
export const FACES = [
    "arch",
    "bean",
    "bloom",
    "bolt",
    "cloud",
    "coil",
    "cube",
    "frame",
    "gem",
    "grid",
    "kite",
    "knot",
    "loop",
    "moon",
    "peak",
    "pleat",
    "prism",
    "ring",
    "spark",
    "spool",
    "sprout",
    "stack",
    "wave",
    "wedge",
] as const;

export type Face = (typeof FACES)[number];

/** The built plugins' faces, picked by hand: the calendar-like grid for
 *  Meetings, the star of the morning for Today, the stack of notes for Tasks,
 *  and so on. Each is worn by one plugin only. */
const PICKED: Record<string, Face> = {
    mail: "coil",
    meetings: "grid",
    today: "spark",
    "people-map": "knot",
    tasks: "stack",
    "deal-tracker": "gem",
    "booking-page": "moon",
    "lead-research": "kite",
    "brand-kit": "prism",
    "deck-maker": "frame",
};

/** The plugins not built yet are dealt the faces no built one wears, in build
 *  order, so a face seen on a working plugin always means that plugin. Within
 *  a line no face repeats: a line has fewer plugins than there are faces left. */
const DEALT: ReadonlyMap<string, Face> = (() => {
    const worn = new Set<Face>(Object.values(PICKED));
    const free = FACES.filter((f) => !worn.has(f));
    const dealt = new Map<string, Face>();
    const inLine = new Map<string, Set<Face>>();
    let next = 0;
    for (const plugin of PLANNED) {
        const taken = inLine.get(plugin.line) ?? new Set<Face>();
        inLine.set(plugin.line, taken);
        let face = free[next % free.length];
        for (let i = 0; i < free.length && taken.has(face); i++) {
            next++;
            face = free[next % free.length];
        }
        next++;
        taken.add(face);
        dealt.set(plugin.slug, face);
    }
    return dealt;
})();

export function faceOf(slug: string): Face {
    return PICKED[slug] ?? DEALT.get(slug) ?? "loop";
}

/** The face as an image: 512px, or 128px with `small`. */
export function faceSrc(slug: string, small = true): string {
    return `/teammates/cast/${faceOf(slug)}${small ? "-sm" : ""}.webp`;
}

/** Every built plugin has a face picked for it. */
export function pickedFor(): string[] {
    return BUILT.map((p) => p.slug).filter((slug) => PICKED[slug]);
}
