import type { Metadata } from "next";
import Link from "next/link";
import s from "./landing.module.css";
import { Hero } from "./hero";
import { Shared, Examples, Access, Thinks, Behind, Closing } from "./sections";
import { ToTheApp } from "./to-the-app";
import { LemmaLogo } from "@/ui/icons";

export const metadata: Metadata = {
    title: "Lemma — Hire an AI teammate",
    description: "Hire an AI teammate for ongoing work. Give it responsibility, teach it how your team works, and build the tools for the job together.",
};

/** A character-led front door: meet the teammate, follow its work, look inside. */
export default function Home() {
    return (
        <div className={s.page}>
            <ToTheApp />
            <a className={s.skip} href="#main">Skip to content</a>

            <header className={s.nav}>
                <Link href="/" className={s.wordmark} aria-label="Lemma home">
                    <LemmaLogo />
                </Link>
                <nav aria-label="Main navigation">
                    <a href="#examples">Examples</a>
                    <a href="#how-it-works">How it works</a>
                    <a href="https://github.com/lemma-work" target="_blank" rel="noreferrer">GitHub</a>
                    <Link href="/t">Get started</Link>
                </nav>
                <details className={s.mobileMenu}>
                    <summary>Menu</summary>
                    <div><a href="#examples">Examples</a><a href="#how-it-works">How it works</a><a href="https://github.com/lemma-work" target="_blank" rel="noreferrer">GitHub ↗</a></div>
                </details>
            </header>

            <main id="main">
                <Hero />
                <Shared />
                <Examples />
                <Access />
                <Thinks />
                <Behind />
                <Closing />
            </main>

            <footer className={s.footer}>
                <Link className={s.wordmark} href="/"><LemmaLogo /></Link>
                <span>People use the app. Agents work through it.</span>
                <Link href="/t">Get started ↗</Link>
            </footer>
        </div>
    );
}
