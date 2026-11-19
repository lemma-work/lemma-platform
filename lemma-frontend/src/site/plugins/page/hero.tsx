"use client";

import { useState, type ReactNode } from "react";
import { DemoFrame } from "./demo-frame";
import s from "./page.module.css";

export interface HeroDemo {
    src: string;
    /** Said under it while it runs: what it is, and how to move around it. */
    note: string;
}

/** A plugin's hero: its words on the left and, on the right, its first real
 *  picture. With a demo, "Try it" swaps the picture for the app itself, on
 *  sample data, in the same place; on a phone the two stack. The demo loads
 *  only when asked for, so the page stays light and the wheel stays the
 *  page's until somebody chooses to look around. */
export function Hero({
    name,
    words,
    soon,
    after,
    poster,
    caption,
    demo,
}: {
    name: string;
    /** Breadcrumb, headline and intro. */
    words: ReactNode;
    /** The add button, or what stands in for it. */
    soon: ReactNode;
    /** Anything under the buttons. */
    after: ReactNode;
    poster: ReactNode;
    caption: string;
    demo: HeroDemo | null;
}) {
    const [trying, setTrying] = useState(false);
    return (
        <header className={s.hero} data-trying={trying ? "" : undefined}>
            <div className={s.heroWords}>
                {words}
                <div className={s.actions}>
                    {demo && (
                        <button type="button" className={s.tryButton} aria-pressed={trying} onClick={() => setTrying(!trying)}>
                            {trying ? "Back to the picture" : "Try it"}
                        </button>
                    )}
                    {soon}
                </div>
                {after}
            </div>
            <figure className={s.stage}>
                <div className={s.stageFrame} data-live={trying ? "" : undefined}>
                    {trying && demo ? (
                        <DemoFrame className={s.demo} src={demo.src} title={`${name}, running on made-up sample data`} />
                    ) : demo ? (
                        <button type="button" className={s.poster} onClick={() => setTrying(true)} aria-label={`Try ${name} on made-up sample data`}>
                            {poster}
                            <span className={s.posterTry} aria-hidden="true">
                                Try it
                            </span>
                        </button>
                    ) : (
                        poster
                    )}
                </div>
                <figcaption className={s.stageCaption}>{trying && demo ? demo.note : caption}</figcaption>
            </figure>
        </header>
    );
}
