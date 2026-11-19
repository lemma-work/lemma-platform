"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import m from "@/app/(marketing)/landing.module.css";
import p from "@/app/(marketing)/product.module.css";
import type { Visual as VisualData } from "../catalog";
import { DemoFrame } from "./demo-frame";
import { PluginWindow } from "./plugin-window";
import { TRY_HASH } from "./try-hash";
import { Visual } from "./visual";
import l from "./landing.module.css";

/** A plugin's hero, centred the way a product page's is: the label, the
 *  serif promise and how it keeps it, the buttons, then the plugin's own
 *  window playing the hero loop, until "Try it live" puts the demo in that
 *  window instead. The demo loads only when asked for. The closing band's
 *  "Try it live" is a link to `#try`, which opens it here too. */
export function LandingHero({
    name,
    slug,
    label,
    headline,
    sub,
    install,
    visual,
    caption,
    face,
    demo,
}: {
    name: string;
    slug: string;
    label: string;
    headline: string;
    sub: string;
    /** Where adding it starts, once it is published. */
    install: string | null;
    visual: VisualData;
    caption: string;
    face: string;
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
        <section className={`${m.wrap} ${p.hero}`}>
            <p className={m.label}>
                <Link href="/templates">Plugins</Link> · {label}
            </p>
            <h1 className={p.headline}>{headline}</h1>
            <p className={p.intro}>{sub}</p>
            <div className={`${m.ctas} ${p.centred}`}>
                {demo && (
                    <button type="button" className={`${m.primary} ${l.button}`} aria-pressed={trying} onClick={() => (trying ? close() : open(false))}>
                        {trying ? "Back to the video" : "Try it live"}
                    </button>
                )}
                {install ? (
                    <Link className={m.textLink} href={install}>
                        Add to a Lemma →
                    </Link>
                ) : (
                    <span className={l.soon}>Coming soon</span>
                )}
            </div>
            <div ref={stage} className={p.stage} id="try-it">
                <PluginWindow slug={slug} title={name}>
                    {trying && demo ? (
                        <DemoFrame className={l.demo} src={demo.src} title={`${name}, running on made-up sample data`} />
                    ) : (
                        <Visual visual={visual} sizes="(max-width: 1240px) 100vw, 1200px" />
                    )}
                </PluginWindow>
                <p className={p.shown}>
                    <img src={face} alt="" width={28} height={28} />
                    {trying && demo ? demo.note : caption}
                </p>
            </div>
        </section>
    );
}
