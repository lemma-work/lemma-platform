import { SiteFooter, JsonLd } from '@/site/chrome';
import { organizationSchema, webSiteSchema } from '@/site/seo/structured-data';
import { pageMetadata } from '@/site/metadata';
import s from "./landing.module.css";
import { Hero, TryIt } from "./hero";
import { HowItWorks, People, Memory, Trust, OpenSource } from "./sections";
import { Builds } from "./builds";
import { MarketingNav } from "./nav";
import { Lively } from "./lively";
import { WhySpace } from "./why-space";
import { Closing } from "./closing";
import { ToTheApp } from "./to-the-app";

export const metadata = pageMetadata('Lemma — Hire an AI teammate','Hire an open-source AI teammate and share it with your people. It learns from all of you, builds the tools the job needs, and answers each person within their permissions.','/');

/** The front door, for a founder or a small team deciding whether to try
 *  it: the claim over the film and where it answers, the live workspace to
 *  click around in, how it works, what is in its space (each part with a page
 *  of its own under /product), who sees what, what it remembers, where it
 *  answers, why it can be trusted, the code, and one way in. */
export default function Home() {
    return (
        <div className={s.page}>
            <ToTheApp /><JsonLd schema={organizationSchema()} /><JsonLd schema={webSiteSchema()} />
            <a className={s.skip} href="#main">Skip to content</a>

            <MarketingNav over />

            <main id="main">
                <Hero />
                <TryIt />
                <WhySpace />
                <HowItWorks />
                <Builds />
                <People />
                <Memory />
                <Trust />
                <OpenSource />
                <Closing />
            </main>

            <SiteFooter />
            <Lively />
        </div>
    );
}
