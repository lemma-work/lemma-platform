"use client";

import Link from "next/link";
import { useEffect, useReducer, useRef, useState } from "react";
import { CharacterPuppet } from "@/shell/character-puppet";
import { INITIAL_TOUR, TOUR_STEPS, tourReducer } from "./tour-state";
import s from "./landing.module.css";

const CHANNELS = [
    { src: "/connector-logos/slack.svg", name: "Slack" },
    { src: "/connector-logos/teams.svg", name: "Microsoft Teams" },
    { src: "/connector-logos/telegram.svg", name: "Telegram" },
    { src: "/connector-logos/whatsapp.svg", name: "WhatsApp" },
];

/** The tour controls the actual Acme workspace in a separate sample document. */
export function Hero() {
    const [{ step, mode }, dispatch] = useReducer(tourReducer, INITIAL_TOUR);
    const [near, setNear] = useState(false);
    const [ready, setReady] = useState(false);
    const frame = useRef<HTMLElement | null>(null);
    const stage = useRef<HTMLDivElement>(null);
    const viewport = useRef<HTMLDivElement>(null);
    const demo = useRef<HTMLIFrameElement>(null);
    const grown = useRef(false);
    const [isGrown, setIsGrown] = useState(false);
    const current = useRef({ step, mode });
    current.current = { step, mode };

    function showStep(index: number) {
        demo.current?.contentWindow?.postMessage({ type: "lemma-tour:step", step: index }, window.location.origin);
    }

    useEffect(() => {
        const read = (event: MessageEvent) => {
            if (event.origin !== window.location.origin || event.source !== demo.current?.contentWindow) return;
            if (event.data?.type === "lemma-tour:ready") {
                setReady(true);
                if (current.current.mode === "guided") showStep(current.current.step);
            }
            if (event.data?.type === "lemma-tour:interact") dispatch({ type: "explore" });
        };
        window.addEventListener("message", read);
        return () => window.removeEventListener("message", read);
    }, []);

    useEffect(() => {
        if (mode === "guided" && ready) showStep(step);
    }, [step, mode, ready]);

    useEffect(() => {
        const node = viewport.current;
        if (!node) return;
        const resize = () => {
            const width = node.clientWidth;
            const natural = width >= 560 ? Math.max(900, width) : width;
            node.style.setProperty("--preview-width", natural + "px");
            node.style.setProperty("--preview-scale", String(width / natural));
        };
        const observer = new ResizeObserver(resize);
        observer.observe(node);
        resize();
        return () => observer.disconnect();
    }, []);

    // Only the background and composition move. Text geometry stays fixed;
    // scroll progress is written directly, without rendering the React tree.
    useEffect(() => {
        const node = stage.current;
        const field = frame.current;
        if (!node || !field) return;
        const compact = matchMedia("(max-width: 1180px), (max-height: 680px), (prefers-reduced-motion: reduce)");
        let raf = 0;
        function read() {
            raf = 0;
            const run = node!.offsetHeight - innerHeight;
            const through = compact.matches || run <= 0 ? 0 : Math.min(1, Math.max(0, -node!.getBoundingClientRect().top / run));
            const grow = Math.min(1, through / 0.18);
            field!.style.setProperty("--grow", String(grow));
            const nextGrown = grow > 0.5;
            if (nextGrown !== grown.current) {
                grown.current = nextGrown;
                setIsGrown(nextGrown);
            }
            const into = (through - 0.2) / 0.8;
            if (!compact.matches) dispatch({ type: "scroll", step: into < 0 ? -1 : Math.min(TOUR_STEPS.length - 1, Math.floor(into * TOUR_STEPS.length)) });
        }
        function schedule() {
            if (!raf && !document.hidden) raf = requestAnimationFrame(read);
        }
        function visibility() {
            cancelAnimationFrame(raf);
            raf = 0;
            if (!document.hidden) read();
        }
        read();
        window.addEventListener("scroll", schedule, { passive: true });
        window.addEventListener("resize", schedule);
        document.addEventListener("visibilitychange", visibility);
        compact.addEventListener("change", read);
        return () => {
            cancelAnimationFrame(raf);
            window.removeEventListener("scroll", schedule);
            window.removeEventListener("resize", schedule);
            document.removeEventListener("visibilitychange", visibility);
            compact.removeEventListener("change", read);
        };
    }, []);


    function goToStep(index: number) {
        dispatch({ type: "step", step: index });
        showStep(index);
        const node = stage.current;
        if (!node || matchMedia("(max-width: 1180px), (max-height: 680px), (prefers-reduced-motion: reduce)").matches) return;
        const run = node.offsetHeight - innerHeight;
        window.scrollTo({ top: node.getBoundingClientRect().top + scrollY + run * (0.2 + (index + 0.3) * 0.8 / TOUR_STEPS.length), behavior: "instant" });
    }

    return <div ref={stage} className={s.heroStage}>
        <section ref={frame} className={`${s.heroField} ${s.toneViolet}`} data-grown={isGrown || undefined}
            onPointerEnter={() => setNear(true)} onPointerLeave={() => setNear(false)}>
            <div className={s.heroLede} inert={isGrown}>
                <p className={s.heroEyebrow}>AI TEAMMATES FOR ONGOING WORK</p>
                <h1 className={s.heroHeadline}>Hire a teammate.</h1>
                <p className={s.heroIntro}>Give it a responsibility. Teach it how your team works.<br />{" "}It learns on the job and builds the tools it needs along the way.</p>
                <div className={s.channels}>
                    <span className={s.channelsLabel}>USE IT FROM</span>
                    {CHANNELS.map(channel => <img key={channel.name} src={channel.src} alt={channel.name} width={26} height={26} />)}
                </div>
                <div className={s.heroActions}>
                    <Link className={s.primary} href="/t">Get started</Link>
                    <a className={s.secondary} href="#examples">Explore an example <span aria-hidden="true">↓</span></a>
                </div>
            </div>
            <div className={s.field}>
                <div className={s.productFrame}>
                    <div className={s.productViewport} ref={viewport}>
                        <iframe ref={demo} src="/demo/landing" title="Explore the Acme workspace" className={s.productIframe} sandbox="allow-scripts allow-same-origin allow-forms" />
                    </div>
                </div>
            </div>
            <div className={s.tourCopy} inert={!isGrown}>
                <p className={s.tourEyebrow}>{mode === "exploring" ? "EXPLORE AT YOUR PACE" : "MEET YOUR TEAMMATE"}</p>
                <ol className={s.beats}>{TOUR_STEPS.map((beat, index) => <li key={beat.name}>
                    <button type="button" className={index === step ? s.beatOn : s.beat} aria-current={index === step ? "step" : undefined} onClick={() => goToStep(index)}>
                        <span className={s.beatNumber}>0{index + 1}</span><span><b>{beat.name}</b><span>{beat.says}</span></span>
                    </button>
                </li>)}</ol>
                <a className={s.tourSkip} href="/demo/landing" target="_blank" rel="noopener noreferrer">Open workspace full screen ↗</a>
            </div>
            <div className={s.knot}><CharacterPuppet character="loop" size={280} greeting={0} mood={near ? "delighted" : "idle"} label="Kit, your launch producer" /></div>
        </section>
        <ol className={s.storySummary}>{TOUR_STEPS.map((beat, index) => <li key={beat.name}><button onClick={() => { goToStep(index); viewport.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" }); }} aria-label={`Show ${beat.name}`}><span>0{index + 1}</span><div><b>{beat.name}</b><p>{beat.says}</p></div></button></li>)}</ol>
    </div>;
}
