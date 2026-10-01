"use client";

import Link from "next/link";
import { useState } from "react";
import { CharacterPuppet } from "@/shell/character-puppet";
import type { CharacterName } from "@/shell/cast";
import { githubUrl } from "@/site/links";
import s from "./landing.module.css";

/** A character that wakes up when you come near it.
 *
 *  Every rig already stops itself when it scrolls out of view, so the cost of
 *  putting several on one page is only ever the ones you can see. What this
 *  adds is the greeting: a wave is a response to somebody arriving, and the
 *  only way a page knows somebody arrived is the pointer. */
function Greeter({ character, size, label }: { character: CharacterName; size: number; label?: string }) {
    const [greeting, setGreeting] = useState(0);
    const [near, setNear] = useState(false);
    return (
        <span
            className={s.greeter}
            onPointerEnter={() => { setNear(true); setGreeting(count => count + 1); }}
            onPointerLeave={() => setNear(false)}
        >
            <CharacterPuppet character={character} size={size} greeting={greeting} mood={near ? "delighted" : "idle"} label={label} />
        </span>
    );
}

export function Closing() {
    return (
        <section className={s.close} aria-labelledby="closing-title">
            <h2 id="closing-title" className={s.closeTitle}>What would you<br />hand over first?</h2>
            <div className={s.ctas}>
                <Link className={s.closePrimary} href="/t">Get started free</Link>
                <a className={s.closeGhost} href={githubUrl} target="_blank" rel="noreferrer">View on GitHub</a>
            </div>
            <div className={s.lineup} aria-hidden="true">
                <Greeter character="moon" size={128} />
                <Greeter character="kite" size={128} />
                <Greeter character="loop" size={128} />
                <Greeter character="gem" size={128} />
                <Greeter character="cloud" size={128} />
            </div>
        </section>
    );
}
