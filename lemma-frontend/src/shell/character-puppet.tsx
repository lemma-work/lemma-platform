"use client";

import dynamic from "next/dynamic";
import type { CharacterName } from "./cast";
import type { Mood } from "@/app/characters/loop/loop-puppet";
import styles from "./character.module.css";

/** The animated character, wherever a mark is big enough to read one.
 *
 *  The rigs already exist — three of them, because Loop, Pleat/Frame and the
 *  twenty-one share nothing but a house style. A static PNG with a hover tilt
 *  was never the plan; it was the fallback, and it made twenty-four sculptures
 *  look like stickers. This picks the right rig and lets the ambient layer in
 *  `app/characters/life.ts` do the rest: breath, sway, arm drift, and a short
 *  beat from the character's own vocabulary every few seconds.
 *
 *  `greeting` is a counter: bump it and the character waves once, then
 *  settles. `mood` is a state it holds — `delighted` keeps the eyes smiling
 *  and the stance open for as long as you stay. A hover wants both: the wave
 *  says hello on arrival, the mood is what it does while you are there.
 *
 *  Loaded lazily and client-only. The rigs are a few hundred lines of
 *  requestAnimationFrame apiece and have no business in the server bundle or
 *  in the first paint of a rail. */

const ExtendedPuppet = dynamic(() => import("@/app/characters/extended-puppet").then(m => m.ExtendedPuppet), { ssr: false });
const CastPuppet = dynamic(() => import("@/app/characters/cast-puppet").then(m => m.CastPuppet), { ssr: false });
const LoopPuppet = dynamic(() => import("@/app/characters/loop/loop-puppet").then(m => m.LoopPuppet), { ssr: false });

export function CharacterPuppet({ character, size, label, className, greeting = 0, mood = "idle" }: {
    character: CharacterName; size: number; label?: string; className?: string; greeting?: number; mood?: Mood;
}) {
    /* The rig takes the rendered size and works out the rest: how far to
       exaggerate the motion so it still reads at this scale, and whether to
       fetch the full art or the small copy. */
    const common = { mood, greeting, paused: false, pixels: size };
    return <span className={styles.puppet + (className ? " " + className : "")} style={{ width: size, height: size }}
        role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
        {character === "loop" ? <LoopPuppet {...common} />
            : character === "pleat" || character === "frame" ? <CastPuppet character={character} {...common} />
            : <ExtendedPuppet character={character} {...common} />}
    </span>;
}
