import { OpenSource } from "@/site/open-source";
import { githubUrl } from "@/site/links";
import { SiteFooter, JsonLd } from '@/site/chrome';
import { organizationSchema, webSiteSchema } from '@/site/seo/structured-data';
import { pageMetadata } from '@/site/metadata';
import Link from "next/link";
import s from "./landing.module.css";
import { Hero } from "./hero";
import { Shared, Examples, Access, Thinks, Behind, Closing } from "./sections";
import { ToTheApp } from "./to-the-app";
import { LemmaLogo } from "@/ui/icons";

export const metadata = pageMetadata('Lemma — Hire an AI teammate','Hire an AI teammate for ongoing work. Give it responsibility, teach it how your team works, and build the tools for the job together.','/');

/** A character-led front door: meet the teammate, follow its work, look inside. */
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
                <Examples />
                <Access />
                <Thinks />
                <Behind />
                <OpenSource />
                <Closing />
            </main>

            <SiteFooter />
        </div>
    );
}
