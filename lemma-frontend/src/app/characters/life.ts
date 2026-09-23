import type { CastMember } from "./extended-cast";

/** Ambient life, shared by all three puppet rigs.
 *
 *  A greeting is a performance: it starts, it peaks, it ends, and every rig
 *  had exactly one of them. Between greetings the body was frozen — lean,
 *  lift, wave and every torso transform damp to zero at rest — so the only
 *  thing still moving was a pair of eyes. That reads as a photograph of a toy
 *  rather than a toy that is awake, and the gallery, where the cards were
 *  rendered paused, read as a shelf.
 *
 *  So: a layer that never stops. `breath`, `sway` and `drift` are continuous
 *  and built from sines with deliberately incommensurate periods, which is
 *  what keeps a four-second loop from announcing itself as a four-second
 *  loop. On top of them a *beat* fires every few seconds — one short move
 *  pulled from the character's own vocabulary, over in about a second where a
 *  greeting takes three or four.
 *
 *  The two run at once rather than taking turns, which is the whole point. A
 *  nod lands mid-breath; a blink lands mid-hop; the sway carries on
 *  underneath a greeting. Each rig reads the channels it has limbs for and
 *  ignores the rest, adding them to whatever the greeting is already doing.
 *
 *  Amplitudes here are normalised to roughly -1..1. Turning them into degrees
 *  and pixels is the rig's business, because a nod on Arch and a nod on Stack
 *  are not the same number. */

/** Every greeting, scaled. Three and a half to four and a half seconds was
 *  right when a greeting was the only thing a body ever did; with something
 *  always happening underneath it, a hello that long reads as slow rather
 *  than warm. A multiplier rather than ten rewritten numbers, so the
 *  differences the rigs were tuned for survive. */
export const TEMPO = .72;

/** At or below this many CSS pixels a rig fetches the `-sm` art. 512 square
 *  covers that on a 2x screen and then some, while the full 1254 render is
 *  a megabyte nobody can see. */
export const SMALL_ABOVE = 320;

/** How much to exaggerate ambient motion for a character drawn this small.
 *  Tuned so a settle is roughly two pixels wherever it is drawn. */
export function lifeGain(pixels: number) {
    return Math.max(1, Math.min(5, 150 / pixels));
}

export type Beat =
    | "bob" | "nod" | "twist" | "spring" | "perk"
    | "shuffle" | "hop" | "step" | "fidget" | "glance";

export type Life = {
    /** Continuous, always on. */
    breath: number; sway: number; drift: number;
    /** A beat, zero unless that one is currently firing. */
    bob: number; nod: number; twist: number; squash: number;
    slide: number; hop: number; step: number; fidget: number;
    glanceX: number; glanceY: number;
};

const REST: Life = {
    breath: 0, sway: 0, drift: 0,
    bob: 0, nod: 0, twist: 0, squash: 0,
    slide: 0, hop: 0, step: 0, fidget: 0,
    glanceX: 0, glanceY: 0,
};

/** How often a character does something, and what it is likely to be.
 *
 *  `bag` is drawn from uniformly, so a repeated entry is simply a heavier
 *  weight. Every character keeps `glance` and a plain `bob` in the bag: the
 *  signature move is what makes them themselves, but a creature that only
 *  ever does its signature move is a wind-up toy. */
type Temperament = { rate: number; breath: number; bag: Beat[] };

const TEMPERAMENTS: Record<CastMember, Temperament> = {
    loop: { rate: 3.4, breath: 1, bag: ["bob", "glance", "fidget", "twist", "glance", "bob"] },
    pleat: { rate: 4.6, breath: .85, bag: ["bob", "glance", "shuffle", "glance", "bob"] },
    frame: { rate: 3.6, breath: .9, bag: ["bob", "glance", "nod", "fidget", "glance"] },
    knot: { rate: 3.8, breath: 1, bag: ["twist", "bob", "glance", "fidget", "glance"] },
    arch: { rate: 4.8, breath: .8, bag: ["nod", "bob", "glance", "glance", "bob"] },
    kite: { rate: 3.1, breath: 1.1, bag: ["step", "bob", "glance", "hop", "fidget"] },
    coil: { rate: 3.3, breath: 1.2, bag: ["spring", "bob", "glance", "spring", "glance"] },
    bloom: { rate: 3.9, breath: 1, bag: ["perk", "bob", "glance", "fidget", "glance"] },
    stack: { rate: 5, breath: .75, bag: ["shuffle", "bob", "glance", "glance", "bob"] },
    spark: { rate: 2.8, breath: 1.15, bag: ["hop", "bob", "glance", "fidget", "hop"] },

    wedge: { rate: 4.8, breath: .8, bag: ["shuffle", "bob", "glance", "glance", "bob"] },
    cloud: { rate: 5.2, breath: 1.15, bag: ["perk", "bob", "glance", "glance", "perk"] },
    bolt: { rate: 2.6, breath: 1.1, bag: ["hop", "fidget", "glance", "hop", "bob"] },
    ring: { rate: 3.7, breath: .95, bag: ["twist", "bob", "glance", "twist", "fidget"] },
    sprout: { rate: 3.9, breath: 1.05, bag: ["perk", "bob", "glance", "fidget", "glance"] },
    cube: { rate: 5, breath: .7, bag: ["nod", "bob", "glance", "glance", "bob"] },
    wave: { rate: 3.5, breath: 1.15, bag: ["twist", "bob", "glance", "twist", "fidget"] },
    moon: { rate: 5.2, breath: .75, bag: ["nod", "glance", "bob", "glance", "bob"] },
    prism: { rate: 3.8, breath: .9, bag: ["twist", "bob", "glance", "nod", "glance"] },
    bean: { rate: 4.6, breath: 1.2, bag: ["spring", "bob", "glance", "glance", "bob"] },
    spool: { rate: 3.6, breath: 1, bag: ["spring", "twist", "bob", "glance", "fidget"] },
    grid: { rate: 5.1, breath: .7, bag: ["shuffle", "bob", "glance", "glance", "nod"] },
    peak: { rate: 3, breath: .95, bag: ["step", "bob", "glance", "hop", "fidget"] },
    gem: { rate: 3.1, breath: 1, bag: ["spring", "fidget", "glance", "bob", "spring"] },
};

/** How long each beat runs, in seconds. Short on purpose — these are asides,
 *  not performances, and a greeting is the only thing allowed to take its
 *  time. A glance holds longest because looking somewhere and looking
 *  straight back is a twitch, not a thought. */
const SPANS: Record<Beat, number> = {
    bob: .95, nod: 1.1, twist: 1.25, spring: .9, perk: 1,
    shuffle: 1.15, hop: .8, step: 1.05, fidget: 1.2, glance: 1.7,
};

/** One life per mounted puppet.
 *
 *  Stateful because the beat schedule is: which move, when it started, when
 *  the next one is due. The phase offset is per instance so that seven cards
 *  in a grid breathe independently instead of pulsing in unison, which is the
 *  single most artificial thing a row of these can do. */
export function createLife(character: CastMember) {
    const temperament = TEMPERAMENTS[character];
    const phase = Math.random() * 60;
    const bag = temperament.bag;
    let beat: Beat = "bob";
    let beatAt = -100;
    let dueAt = 1.2 + Math.random() * temperament.rate;
    let toX = 0, toY = 0;

    /** `awake` gates everything; `busy` holds beats back while the greeting
     *  has the stage, without stopping the breath underneath it.
     *
     *  `gain` scales the body channels for how big the character is actually
     *  drawn. The amplitudes below were tuned on a 620px stage, where a breath
     *  is two pixels; in a 32px rail mark the same numbers are a fifth of a
     *  pixel and every translation disappears, leaving only the rotations —
     *  which is how a rig with arms and legs ends up reading as a shrug. The
     *  gaze is left alone: it is already relative to the eye. */
    return function life(time: number, awake: boolean, busy: boolean, gain = 1): Life {
        if (!awake) return REST;
        const clock = time + phase;
        const out: Life = {
            ...REST,
            // Two periods, roughly 3.4s and 8.2s, so the chest never quite
            // repeats the same rise twice running.
            breath: (Math.sin(clock * 1.85) * .78 + Math.sin(clock * .766) * .22) * temperament.breath * gain,
            sway: (Math.sin(clock * .41) * .62 + Math.sin(clock * .631 + 1.7) * .38) * Math.min(gain, 1.8),
            drift: (Math.sin(clock * .83 + .6) * .56 + Math.sin(clock * .373 + 2.2) * .44) * gain,
        };

        if (busy) {
            // Don't let a beat fire the instant a greeting lets go of the body.
            dueAt = Math.max(dueAt, time + .7);
            return out;
        }
        if (time > dueAt) {
            beat = bag[Math.floor(Math.random() * bag.length)];
            beatAt = time;
            dueAt = time + SPANS[beat] + temperament.rate * (.6 + Math.random() * .9);
            const side = Math.random() < .5 ? -1 : 1;
            toX = side * (.45 + Math.random() * .55);
            toY = (Math.random() - .65) * .8;
        }

        const p = (time - beatAt) / SPANS[beat];
        if (p <= 0 || p >= 1) return out;
        // Rotations already read at any size; only lift and slide need the gain.
        const envelope = Math.sin(p * Math.PI), turn = Math.min(gain, 1.8);
        switch (beat) {
            // A single settle of the weight — the default "still alive".
            case "bob": out.bob = envelope * gain; break;
            // Down, up, done.
            case "nod": out.nod = Math.sin(p * Math.PI * 2) * envelope * turn; break;
            case "twist": out.twist = Math.sin(p * Math.PI * 2) * envelope * turn; break;
            // Compress, then a rebound that runs out of energy.
            case "spring": out.squash = Math.sin(p * Math.PI * 3) * (1 - p) * gain; break;
            case "perk": out.squash = -envelope * gain; break;
            case "shuffle": out.slide = Math.sin(p * Math.PI * 2) * envelope * gain; break;
            // Fuller than a sine so the top of the arc hangs for a moment.
            case "hop": out.hop = Math.pow(envelope, .68) * gain; break;
            case "step": out.step = envelope * turn; break;
            case "fidget": out.fidget = Math.sin(p * Math.PI * 2) * envelope * turn; break;
            // Look away, hold, look back: a plateau rather than a peak.
            case "glance": {
                const hold = Math.min(1, envelope * 2.3);
                out.glanceX = toX * hold; out.glanceY = toY * hold;
                break;
            }
        }
        return out;
    };
}
