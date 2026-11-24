"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import { key } from "@/session/storage";
import { LemmaLogo } from "@/ui/icons";

import { CHAPTERS } from "./contents";

/**
 * The guide's header, and everything on the page that needs the browser.
 *
 * The header stays at the top while you read. It says which chapter you are
 * in, opens the contents (every chapter and section, with yours marked), and
 * holds the light/dark switch, which writes the same preference the app
 * reads. A line along its foot shows how far through the guide you are.
 *
 * It also brings each block and figure in once as it is reached. The page is
 * complete without that: blocks are hidden only after this has run and added
 * `gb-live`, so a reader with scripts off, or one who asked for reduced
 * motion, gets every word and figure in its finished state.
 */
export function GuideHeader() {
    const head = useRef<HTMLElement>(null);
    const bar = useRef<HTMLSpanElement>(null);
    const toc = useRef<HTMLDivElement>(null);
    const [where, setWhere] = useState<{ chapter: number; section: string }>({ chapter: 0, section: "" });
    const [dark, setDark] = useState(false);

    useEffect(() => {
        const root = document.documentElement;
        const current = () => {
            const chosen = root.dataset.theme;
            if (chosen === "dark" || chosen === "light") return chosen;
            return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        };
        setDark(current() === "dark");
    }, []);

    /* Where the reader is: the last chapter and section whose top has passed under the header. */
    useEffect(() => {
        const root = document.documentElement;
        const line = bar.current;
        const bands = [...document.querySelectorAll<HTMLElement>(".gb-band[data-chapter]")];
        const sections = [...document.querySelectorAll<HTMLElement>(".gb-s[id]")];
        let frame = 0;
        const measure = () => {
            frame = 0;
            const edge = (head.current?.offsetHeight ?? 56) + 24;
            let chapter = 0;
            for (const band of bands) if (band.getBoundingClientRect().top <= edge) chapter = Number(band.dataset.chapter);
            let section = "";
            for (const each of sections) if (each.getBoundingClientRect().top <= edge) section = each.id;
            setWhere((was) => (was.chapter === chapter && was.section === section ? was : { chapter, section }));
            if (line) {
                const distance = root.scrollHeight - window.innerHeight;
                line.style.transform = `scaleX(${distance > 0 ? Math.min(1, window.scrollY / distance) : 0})`;
            }
        };
        const soon = () => {
            if (!frame) frame = requestAnimationFrame(measure);
        };
        measure();
        window.addEventListener("scroll", soon, { passive: true });
        window.addEventListener("resize", soon);
        return () => {
            cancelAnimationFrame(frame);
            window.removeEventListener("scroll", soon);
            window.removeEventListener("resize", soon);
        };
    }, []);

    /* The contents open on the chapter being read, not on chapter one. */
    useEffect(() => {
        const panel = toc.current;
        if (!panel) return;
        const opened = (event: Event) => {
            if ((event as ToggleEvent).newState !== "open") return;
            const here = panel.querySelector<HTMLElement>(".gb-on");
            panel.scrollTop = here ? here.offsetTop - 8 : 0;
        };
        panel.addEventListener("toggle", opened);
        return () => panel.removeEventListener("toggle", opened);
    }, []);

    /* Each block and figure, brought in once as it is reached. */
    useEffect(() => {
        const guide = head.current?.closest<HTMLElement>(".guidebook");
        if (!guide) return;
        const figures = guide.querySelectorAll(".gb-fig");
        const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (!("IntersectionObserver" in window) || still) {
            for (const figure of figures) figure.classList.add("gb-in");
            return;
        }
        guide.classList.add("gb-live");
        const reached = new IntersectionObserver(
            (entries) => {
                let order = 0;
                for (const entry of entries) {
                    if (!entry.isIntersecting) continue;
                    const block = entry.target as HTMLElement;
                    block.style.setProperty("--i", String(Math.min(order++, 4)));
                    block.classList.add("gb-in");
                    reached.unobserve(block);
                }
            },
            { threshold: 0.06, rootMargin: "0px 0px -6% 0px" },
        );
        for (const block of guide.querySelectorAll(".gb-c > *, .gb-band h2, .gb-band .gb-k")) {
            block.classList.add("gb-rv");
            reached.observe(block);
        }
        return () => {
            reached.disconnect();
            guide.classList.remove("gb-live");
        };
    }, []);

    const flip = () => {
        const next = dark ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try {
            localStorage.setItem(key("theme"), next);
        } catch {
            // A browser that refuses storage still gets the switch for this visit.
        }
        setDark(!dark);
    };

    const open = CHAPTERS.find((chapter) => chapter.n === where.chapter);

    return (
        <>
            <header ref={head} className="gb-head">
                <div className="gb-head-in">
                    <Link href="/" className="gb-home" aria-label="Lemma home">
                        <LemmaLogo />
                    </Link>
                    <p className="gb-where">
                        <a href="#top">The guide</a>
                        {open && (
                            <span className="gb-where-ch">
                                <span className="gb-where-n">{open.n}</span>
                                <span className="gb-where-t">{open.title}</span>
                            </span>
                        )}
                    </p>
                    <button type="button" className="gb-head-b" popoverTarget="gb-toc">
                        Contents
                    </button>
                    <Link href="/docs" className="gb-head-a">
                        Docs
                    </Link>
                    <button type="button" className="gb-theme" aria-pressed={dark} aria-label={dark ? "Switch to light" : "Switch to dark"} onClick={flip}>
                        <i />
                    </button>
                </div>
                <span ref={bar} className="gb-prog" aria-hidden="true" />
            </header>
            <div
                ref={toc}
                id="gb-toc"
                className="gb-tocpop"
                popover="auto"
                onClick={(event) => {
                    if ((event.target as HTMLElement).closest("a")) toc.current?.hidePopover();
                }}
            >
                <nav aria-label="Contents">
                    <ol>
                        {CHAPTERS.map((chapter) => (
                            <li key={chapter.id} className={chapter.n === where.chapter ? "gb-on" : undefined}>
                                <a href={`#${chapter.id}`} aria-current={chapter.n === where.chapter && !where.section.length ? "location" : undefined}>
                                    <span className="gb-n">{chapter.n}</span>
                                    <span>{chapter.title}</span>
                                </a>
                                <ol>
                                    {chapter.sections.map((each) => (
                                        <li key={each.id}>
                                            <a href={`#${each.id}`} aria-current={each.id === where.section ? "location" : undefined}>
                                                <span className="gb-sn">{each.n}</span>
                                                <span>{each.title}</span>
                                            </a>
                                        </li>
                                    ))}
                                </ol>
                            </li>
                        ))}
                    </ol>
                </nav>
            </div>
        </>
    );
}
