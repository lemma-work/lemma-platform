import Link from "next/link";
import { githubUrl } from "@/site/links";
import { SiteFooter, JsonLd } from '@/site/chrome';
import { organizationSchema, webSiteSchema } from '@/site/seo/structured-data';
import { pageMetadata } from '@/site/metadata';
import { LemmaLogo } from "@/ui/icons";
import s from "./landing.module.css";
import { Hero, TryIt } from "./hero";
import { Channels, HowItWorks, Builds, People, Memory, Trust, OpenSource } from "./sections";
import { Closing } from "./closing";
import { ToTheApp } from "./to-the-app";

export const metadata = pageMetadata('Lemma — Hire an AI teammate. Give it a space.','Hire an open-source AI teammate and give it a space for its job. It builds the tools the job needs, answers each person within their permissions, and writes down what it learns.','/');

/** The front door, for a founder or a small team deciding whether to try
 *  it: the claim over the film, where it answers, the live workspace to
 *  click around in, how it works, the apps it builds, who sees what, how it
 *  learns, why it can be trusted, the code, and one way in. */
export default function Home() {
    return (
        <div className={s.page}>
            <ToTheApp /><JsonLd schema={organizationSchema()} /><JsonLd schema={webSiteSchema()} />
            <a className={s.skip} href="#main">Skip to content</a>

            <header className={s.nav}>
                <div className={`${s.wrap} ${s.navInner}`}>
                    <Link href="/" className={s.wordmark} aria-label="Lemma home">
                        <LemmaLogo />
                    </Link>
                    <nav aria-label="Main navigation" className={s.navLinks}>
                        <Link href="/docs">Docs</Link>
                        <Link href="/templates">Templates</Link>
                        <a href={githubUrl} target="_blank" rel="noreferrer">GitHub</a>
                        <Link href="/t">Sign in</Link>
                        <Link href="/t" className={s.navStart}>Get started</Link>
                    </nav>
                    <details className={s.mobileMenu}>
                        <summary>Menu</summary>
                        <div>
                            <Link href="/docs">Docs</Link>
                            <Link href="/templates">Templates</Link>
                            <a href={githubUrl} target="_blank" rel="noreferrer">GitHub ↗</a>
                            <Link href="/t">Sign in</Link>
                        </div>
                    </details>
                </div>
            </header>

            <main id="main">
                <Hero />
                <Channels />
                <TryIt />
                <HowItWorks />
                <Builds />
                <People />
                <Memory />
                <Trust />
                <OpenSource />
                <Closing />
            </main>

            <SiteFooter />
        </div>
    );
}
