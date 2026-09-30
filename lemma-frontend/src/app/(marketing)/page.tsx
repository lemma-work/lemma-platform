import { OpenSource } from "@/site/open-source";
import { githubUrl } from "@/site/links";
import { SiteFooter, JsonLd } from '@/site/chrome';
import { organizationSchema, webSiteSchema } from '@/site/seo/structured-data';
import { pageMetadata } from '@/site/metadata';
import Link from "next/link";
import s from "./landing.module.css";
import { Hero } from "./hero";
import { Shared, Examples, Team, Thinks, Behind, Closing } from "./sections";
import { ToTheApp } from "./to-the-app";
import { LemmaLogo } from "@/ui/icons";

export const metadata = pageMetadata('Lemma — The AI teammate your whole team shares','An AI teammate for ongoing work that your whole team shares. It builds the tools the job needs, answers each person within their permissions, and writes down what it learns.','/');

/** A character-led front door: the teammate everyone shares, what it builds,
 *  a team of them, where to reach them, and how they run. */
export default function Home() {
    return (
        <div className={s.page}>
            <ToTheApp /><JsonLd schema={organizationSchema()} /><JsonLd schema={webSiteSchema()} />
            <a className={s.skip} href="#main">Skip to content</a>

            <header className={s.nav}>
                <Link href="/" className={s.wordmark} aria-label="Lemma home">
                    <LemmaLogo />
                </Link>
                <nav aria-label="Main navigation">
                    <Link href="/docs">Docs</Link><Link href="/templates">Examples</Link>
                    <a href="#how-it-works">How it works</a>
                    <a href={githubUrl} target="_blank" rel="noreferrer">GitHub</a>
                    <Link href="/t">Get started</Link>
                </nav>
                <details className={s.mobileMenu}>
                    <summary>Menu</summary>
                    <div><Link href="/docs">Docs</Link><Link href="/templates">Examples</Link><a href="#open-source">Open source</a><a href="#how-it-works">How it works</a><a href={githubUrl} target="_blank" rel="noreferrer">GitHub ↗</a></div>
                </details>
            </header>

            <main id="main">
                <Hero />
                <Shared />
                <Behind />
                <Team />
                <Examples />
                <Thinks />
                <OpenSource />
                <Closing />
            </main>

            <SiteFooter />
        </div>
    );
}
