"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { DemoFrame } from "../page/demo-frame";
import { TRY_HASH } from "../page/try-hash";
import p from "./plugin.module.css";
import s from "./store.module.css";

/** A plugin's hero: where it sits, its promise and how it keeps it, the two
 *  buttons, then the plugin's window showing the real app, until "Try it
 *  here" puts the running demo in that window instead. The demo loads only
 *  when asked for; a link to `#try` (the index's "Try it", the closing band's
 *  button) opens it too. */
export function PluginHero({
    name,
    face,
    crumb,
    headline,
    sub,
    install,
    picture,
    caption,
    demo,
}: {
    name: string;
    face: string;
    /** What follows "Plugins /" over the headline. */
    crumb: string;
    headline: string;
    sub: string;
    install: string | null;
    picture: ReactNode;
    caption: string;
    demo: { src: string; note: string } | null;
}) {
    const [trying, setTrying] = useState(false);
    const stage = useRef<HTMLDivElement>(null);

    const open = useCallback((scroll: boolean) => {
        setTrying(true);
        if (scroll) {
            const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            stage.current?.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "center" });
        }
    }, []);

    useEffect(() => {
        if (!demo) return;
        const follow = () => {
            if (window.location.hash === TRY_HASH) open(true);
        };
        follow();
        window.addEventListener("hashchange", follow);
        return () => window.removeEventListener("hashchange", follow);
    }, [demo, open]);

    const close = () => {
        setTrying(false);
        if (window.location.hash === TRY_HASH) history.replaceState(null, "", window.location.pathname + window.location.search);
    };

    return (
        <header className={p.hero}>
            <p className={s.eyebrow}>
                <Link href="/templates">plugins</Link> <span aria-hidden="true">/</span> {crumb}
            </p>
            <p className={p.name}>
                <img src={face} alt="" width={40} height={40} />
                {name}
            </p>
            <h1 className={p.title}>{headline}</h1>
            <p className={s.intro}>{sub}</p>
            <p className={p.acts}>
                {demo && (
                    <button type="button" className={s.primary} aria-pressed={trying} onClick={() => (trying ? close() : open(false))}>
                        {trying ? "Back to the picture" : "Try it here"}
                    </button>
                )}
                {install ? (
                    <Link className={p.ghost} href={install}>
                        Add to a Lemma
                    </Link>
                ) : (
                    <span className={p.soon}>You can add it soon</span>
                )}
            </p>
            <div ref={stage} className={p.stage} id="try-it">
                <div className={s.window}>
                    <span className={s.windowBar} aria-hidden="true">
                        <i />
                        <i />
                        <i />
                        <span>{name}</span>
                    </span>
                    {trying && demo ? <DemoFrame className={p.demo} src={demo.src} title={`${name}, running on made-up sample data`} /> : picture}
                </div>
                <p className={p.caption}>{trying && demo ? demo.note : caption}</p>
            </div>
        </header>
    );
}
