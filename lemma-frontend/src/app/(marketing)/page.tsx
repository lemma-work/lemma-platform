import { SiteFooter, JsonLd } from '@/site/chrome';
import { organizationSchema, webSiteSchema } from '@/site/seo/structured-data';
import { pageMetadata } from '@/site/metadata';
import s from "./landing.module.css";
import { Hero } from "./hero";
import { Learns, Yours, Close } from "./sections";
import { Builds } from "./builds";
import { MarketingNav } from "./nav";
import { Lively } from "./lively";
import { Daylight } from "./daylight";
import { ToTheApp } from "./to-the-app";

export const metadata = pageMetadata('Lemma — Hire an AI teammate','Hire an open-source AI teammate and share it with your people. It learns from all of you, builds the tools the job needs, and answers each person within their permissions.','/');

/** The front door, for a founder or a small team deciding whether to try
 *  it: the claim over the platform itself, what it is made of and where
 *  people ask it from; who shares it and what it learned from them; what it
 *  builds, part by part; why it stays yours; and one way in. */
export default function Home() {
    return (
        <div className={s.page}>
            <ToTheApp /><JsonLd schema={organizationSchema()} /><JsonLd schema={webSiteSchema()} />
            <a className={s.skip} href="#main">Skip to content</a>
            <Daylight />

            <MarketingNav over />

            <main id="main">
                <Hero />
                <Learns />
                <Builds />
                <Yours />
                <Close />
            </main>

            <SiteFooter />
            <Lively />
        </div>
    );
}
