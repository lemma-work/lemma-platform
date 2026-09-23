"use client";

import { useEffect, useState } from "react";
import { markTint } from "@/shell/mark";

/* How far the core and its halo travel at full amplitude. The halo moves
   further because it is the part read as loudness; the core moving that much
   reads as a button being pressed. */
const CORE_TRAVEL = 0.22;
const HALO_TRAVEL = 0.55;

/** True while the person has asked for less movement.
 *
 *  Worth a hook rather than a CSS rule. The global reduced-motion kill switch
 *  zeroes `animation-duration` and `transition-duration`, which is enough for
 *  everything else in this app and does nothing here: the mark's amplitude is
 *  a `transform` written from JS on every audio chunk, and a transform with no
 *  transition still jumps. Left alone it would be the one thing on screen that
 *  ignored the setting. */
function useStillness(): boolean {
    const [still, setStill] = useState(false);
    useEffect(() => {
        const query = window.matchMedia("(prefers-reduced-motion: reduce)");
        const read = () => setStill(query.matches);
        read();
        query.addEventListener("change", read);
        return () => query.removeEventListener("change", read);
    }, []);
    return still;
}

/** The face of a call: the teammate's own seeded mark, breathing with the
 *  voice. No video, no avatar — the screen beside it is for the work.
 *
 *  One component for both sizes because the amplitude is the identity of the
 *  thing. A bar and a screen showing the same voice at different rhythms
 *  would read as two different calls. */
export function CallMark({ teammate, level, size }: { teammate: string; level: number; size: "bar" | "stage" }) {
    const still = useStillness();
    const amplitude = still ? 0 : Math.min(level, 1);

    return (
        <span className={"call-mark call-mark--" + size} aria-hidden="true">
            <span
                className="call-mark__halo"
                style={{ ...markTint(teammate), transform: `scale(${1 + amplitude * HALO_TRAVEL})` }}
            />
            <span
                className="call-mark__core"
                style={{ ...markTint(teammate), transform: `scale(${1 + amplitude * CORE_TRAVEL})` }}
            />
        </span>
    );
}
