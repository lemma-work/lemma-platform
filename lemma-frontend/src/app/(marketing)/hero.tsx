"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { CharacterPuppet } from "@/shell/character-puppet";
import { WorkspaceLoading } from "@/shell/workspace-loading";
import s from "./landing.module.css";

/** The loop behind the headline: a work campus in a better future, where
 *  people and their teammates get on with the job together. Rendered with
 *  Gemini Omni from the cast's character sheets and played forward and back
 *  over a stretch where nobody moves far, so it never seams. It fills the
 *  right of the hero only, so the words sit on clean ground and the
 *  characters stay solid. */
const FILM = { mp4: "/landing/hero-campus.mp4", webm: "/landing/hero-campus.webm", poster: "/landing/hero-campus-poster.webp" };

/** The claim, over that world. */
export function Hero() {
    /* Still, for anyone who asked for less motion: the poster, and no play. */
    const film = useRef<HTMLVideoElement>(null);
    useEffect(() => {
        const video = film.current;
        if (!video) return;
        const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
        const apply = () => { if (calm.matches) video.pause(); else void video.play().catch(() => undefined); };
        apply();
        calm.addEventListener("change", apply);
        return () => calm.removeEventListener("change", apply);
    }, []);

    return (
        <section className={s.hero}>
            <video ref={film} className={s.heroFilm} muted loop playsInline preload="auto" poster={FILM.poster} aria-hidden="true">
                <source src={FILM.webm} type="video/webm" />
                <source src={FILM.mp4} type="video/mp4" />
            </video>
            <div className={s.heroWash} aria-hidden="true" />
            <div className={s.wrap}>
                <div className={s.heroText}>
                    <p className={s.label}>Open source</p>
                    <h1 className={s.heroHeadline}><span>Hire an AI teammate.</span><span>Give it a space.</span></h1>
                    <p className={s.heroIntro}>
                        The space is where it keeps the docs, lists and apps for its job, and where your people work with it.
                    </p>
                    <div className={s.ctas}>
                        <Link className={s.primary} href="/t">Get started free</Link>
                        <Link className={s.textLink} href="/contact">Talk to us →</Link>
                    </div>
                </div>
            </div>
        </section>
    );
}

/* ── The live workspace ─────────────────────────────────────────────────
   The actual Acme sample, in its own document, on a dark band. The buttons
   under it put it on a screen worth seeing; clicking into it hands it the
   mouse. Nothing here follows the scroll. */

const TRIES: { step: number; label: string }[] = [
    { step: -1, label: "Ask Kit something" },
    { step: 2, label: "Open Launch studio" },
    { step: 1, label: "See who’s in" },
    { step: 3, label: "Read what Kit remembers" },
    { step: 4, label: "Connect a channel" },
];

export function TryIt() {
    const [step, setStep] = useState(-1);
    const [near, setNear] = useState(false);
    const [greeting, setGreeting] = useState(0);
    const [ready, setReady] = useState(false);
    // A frame that never says it is ready is still shown eventually: its
    // own error is more use to a visitor than a placeholder that never ends.
    const [waitedOut, setWaitedOut] = useState(false);
    useEffect(() => {
        const timer = window.setTimeout(() => setWaitedOut(true), 12_000);
        return () => window.clearTimeout(timer);
    }, []);
    const viewport = useRef<HTMLDivElement>(null);
    const demo = useRef<HTMLIFrameElement>(null);
    const current = useRef(step);
    current.current = step;

    function show(index: number) {
        demo.current?.contentWindow?.postMessage({ type: "lemma-tour:step", step: index }, window.location.origin);
    }

    useEffect(() => {
        const read = (event: MessageEvent) => {
            if (event.origin !== window.location.origin || event.source !== demo.current?.contentWindow) return;
            if (event.data?.type === "lemma-tour:ready") {
                setReady(true);
                show(current.current);
            }
        };
        window.addEventListener("message", read);
        // The frame announces itself once; if that happened before this
        // listener existed, ask again.
        demo.current?.contentWindow?.postMessage({ type: "lemma-tour:hello" }, window.location.origin);
        return () => window.removeEventListener("message", read);
    }, []);

    /* The workspace scrolls inside itself, and a frame takes every wheel
       event over it, so a visitor scrolling the page would get stuck in the
       demo. Until they click into it, a clear layer on top lets the wheel
       reach the page. Clicking hands the demo the mouse and rings the frame;
       leaving it, clicking elsewhere or scrolling it away hands it back. */
    const [engaged, setEngaged] = useState(false);
    useEffect(() => {
        const node = viewport.current;
        if (!node) return;
        const outside = (event: PointerEvent) => { if (!node.contains(event.target as Node)) setEngaged(false); };
        // Keyboard visitors reach the frame by Tab, which never touches the layer.
        const blur = () => window.setTimeout(() => { if (document.activeElement === demo.current) setEngaged(true); });
        const seen = new IntersectionObserver(([entry]) => { if (entry.intersectionRatio < 0.4) setEngaged(false); }, { threshold: [0.4] });
        document.addEventListener("pointerdown", outside);
        window.addEventListener("blur", blur);
        seen.observe(node);
        return () => {
            document.removeEventListener("pointerdown", outside);
            window.removeEventListener("blur", blur);
            seen.disconnect();
        };
    }, []);

    useEffect(() => {
        const node = viewport.current;
        if (!node) return;
        const resize = () => {
            const width = node.clientWidth;
            const natural = width >= 560 ? Math.max(1180, width) : width;
            node.style.setProperty("--preview-width", natural + "px");
            node.style.setProperty("--preview-scale", String(width / natural));
        };
        const observer = new ResizeObserver(resize);
        observer.observe(node);
        resize();
        return () => observer.disconnect();
    }, []);

    function go(index: number) {
        setStep(index);
        if (ready) show(index);
    }

    return (
        <section className={s.band} id="try" aria-labelledby="try-title"
            onPointerEnter={() => { setNear(true); setGreeting(count => count + 1); }}
            onPointerLeave={() => setNear(false)}>
            <div className={s.wrap} style={{ position: "relative" }}>
                <div className={s.head}>
                    <div>
                        <p className={s.label}>Live · sample data</p>
                        <h2 id="try-title" className={s.title}>This is Kit’s space.<br />Go on, click around.</h2>
                    </div>
                    <p className={s.body}>
                        Kit runs launches at Acme, a company we made up. The workspace is the real product: ask it something,
                        open the app it built, read what it has learned.
                    </p>
                </div>
                <div className={s.stage}>
                    <div className={s.stageKit} aria-hidden="true">
                        <CharacterPuppet character="loop" size={132} greeting={greeting} mood={near ? "delighted" : "idle"} />
                    </div>
                    <div className={s.productFrame} data-engaged={engaged || undefined}>
                        <div className={s.productViewport} ref={viewport} data-revealed={ready || waitedOut || undefined}
                            onPointerLeave={event => { if (event.pointerType === "mouse") setEngaged(false); }}>
                            <iframe ref={demo} src="/demo/landing" title="Kit’s space, with sample data" className={s.productIframe} sandbox="allow-scripts allow-same-origin allow-forms" loading="lazy" />
                            {/* The workspace's own loading shape, drawn by this page so it is
                                there on first paint, until the frame has something to show. */}
                            <div className={s.productPoster} aria-hidden="true" inert><WorkspaceLoading /></div>
                            {!engaged && <div className={s.productShield} aria-hidden="true" onClick={() => setEngaged(true)}><span>Click to look around</span></div>}
                        </div>
                    </div>
                </div>
                <div className={s.tries} role="group" aria-label="Show part of Kit’s space">
                    {TRIES.map(one => (
                        <button key={one.step} type="button" className={one.step === step ? s.tryOn : s.try}
                            aria-pressed={one.step === step} onClick={() => go(one.step)}>
                            <em>Try</em>{one.label}
                        </button>
                    ))}
                    <a className={s.tryFull} href="/demo/landing" target="_blank" rel="noopener noreferrer">Open it full screen ↗</a>
                </div>
            </div>
        </section>
    );
}
