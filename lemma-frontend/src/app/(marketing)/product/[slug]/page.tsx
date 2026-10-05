import Link from "next/link";
import { notFound } from "next/navigation";
import { githubUrl } from "@/site/links";
import { SiteFooter } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
import { FACES, PRODUCT_PAGES, productPage, type Picture as Capture, type Section, type Visual, type Who } from "@/marketing/product-pages";
import { MarketingNav } from "../../nav";
import { Lively } from "../../lively";
import { AppWindow, Note, Shot } from "../../pictures";
import { VIGNETTES } from "../../vignettes";
import s from "../../landing.module.css";
import p from "../../product.module.css";

type Props = { params: Promise<{ slug: string }> };

export function generateStaticParams() {
    return PRODUCT_PAGES.map(page => ({ slug: page.slug }));
}

export async function generateMetadata({ params }: Props) {
    const page = productPage((await params).slug);
    return page ? pageMetadata(page.name, page.metaDescription, "/product/" + page.slug) : {};
}

/** One part of a teammate's space, explained for somebody who has never seen
 *  it: what it is and why it exists, then everything it does, each section
 *  beside a real screen or a small moment of the product in motion. The
 *  words are in src/marketing/product-pages.ts. */
export default async function ProductPage({ params }: Props) {
    const page = productPage((await params).slug);
    if (!page) notFound();
    const { hero } = page;
    return (
        <div className={s.page}>
            <a className={s.skip} href="#main">Skip to content</a>
            <MarketingNav />
            <main id="main">
                <section className={`${s.wrap} ${p.hero}`}>
                    <p className={s.label}><Link href="/">Lemma</Link> · {hero.eyebrow}</p>
                    <h1 className={p.headline}>{hero.headline}</h1>
                    <Paragraphs text={hero.intro} className={p.intro} />
                    <div className={`${s.ctas} ${p.centred}`}>
                        <Link className={s.primary} href="/t">Get started free</Link>
                        <Link className={s.textLink} href="/#try">Look around a sample space →</Link>
                    </div>
                    <div className={p.stage}>
                        <Picture visual={hero.visual} big />
                    </div>
                </section>

                {page.sections.map((section, index) => <SectionRow key={section.id} section={section} flip={index % 2 === 1} />)}

                <section className={`${s.wrap} ${s.section}`} aria-labelledby="rest-title">
                    <p className={s.label}>The rest of its space</p>
                    <h2 id="rest-title" className={s.title}>Every part of the job, in one place.</h2>
                    <nav className={p.siblings} aria-label="Product">
                        {PRODUCT_PAGES.map(other => (
                            <Link key={other.slug} href={"/product/" + other.slug} aria-current={other.slug === page.slug ? "page" : undefined}>{other.name}</Link>
                        ))}
                    </nav>
                </section>

                <section className={p.close} data-rise="" aria-labelledby="close-title">
                    <h2 id="close-title">{page.close.headline}</h2>
                    {page.close.sub && <p className={p.closeSub}>{page.close.sub}</p>}
                    <div className={`${s.ctas} ${p.centred}`}>
                        <Link className={p.closePrimary} href="/t">Get started free</Link>
                        <a className={p.closeGhost} href={githubUrl} target="_blank" rel="noreferrer">View on GitHub</a>
                    </div>
                </section>
            </main>
            <SiteFooter />
            <Lively />
        </div>
    );
}

/** The four sample teammates' apps, for a section that shows them together. */
const GALLERY: { title: string; who: Who; picture: Capture }[] = [
    { title: "Feedback loop", who: "Kit", picture: { kind: "still", src: "/landing/app-kit.webp", width: 2560, height: 1440, alt: "Feedback loop: every theme users report, its fix, and who still needs to hear back." } },
    { title: "Customer launchpad", who: "June", picture: { kind: "still", src: "/landing/app-june.webp", width: 2560, height: 1440, alt: "Customer launchpad: a customer’s file checked before it imports." } },
    { title: "Deal desk", who: "Remy", picture: { kind: "still", src: "/landing/app-remy.webp", width: 2560, height: 1440, alt: "Deal desk: every buyer’s next step, with the reply drafted for review." } },
    { title: "Evidence notebook", who: "Scout", picture: { kind: "still", src: "/landing/app-scout.webp", width: 2560, height: 1440, alt: "Evidence notebook: a research question with the evidence and the doubts beside it." } },
];

/** Paragraphs split on blank lines, so the copy can breathe. */
function Paragraphs({ text, className }: { text: string; className?: string }) {
    return <>{text.split(/\n\s*\n/).map((para, index) => <p key={index} className={className}>{para}</p>)}</>;
}

/** A section: its words beside its picture, or, with no picture, its words
 *  centred with its points laid out under them. */
function SectionRow({ section, flip }: { section: Section; flip: boolean }) {
    const points = section.points?.length ? (
        <ul className={p.points}>
            {section.points.map(point => <li key={point.title} data-rise=""><b>{point.title}</b><span>{point.body}</span></li>)}
        </ul>
    ) : null;
    if (section.visual.type === "none") {
        return (
            <section className={`${s.wrap} ${s.section} ${p.saying}`} aria-labelledby={section.id}>
                <div data-rise="">
                    <p className={s.label}>{section.label}</p>
                    <h2 id={section.id} className={s.title}>{section.title}</h2>
                    <Paragraphs text={section.body} className={p.proofBody} />
                </div>
                {points}
            </section>
        );
    }
    /* Three areas: the words, the picture, the points. Beside each other on a
       wide screen, with the picture held in view while the points scroll by;
       on a phone the picture comes straight after the words it shows. */
    return (
        <section className={`${s.wrap} ${s.section} ${p.proof} ${flip ? p.flip : ""}`} aria-labelledby={section.id}>
            <div className={p.words} data-rise="">
                <p className={s.label}>{section.label}</p>
                <h2 id={section.id} className={p.proofTitle}>{section.title}</h2>
                <Paragraphs text={section.body} className={p.proofBody} />
            </div>
            <div className={p.picture}><Picture visual={section.visual} tone={section.tone} /></div>
            {points && <div className={p.pointsArea}>{points}</div>}
        </section>
    );
}

/** A section's picture: a real screen or recording on a panel, or a
 *  vignette, a small moment of the product in motion. A short caption is
 *  written in the margin by hand; a longer one sits quietly underneath. */
function Picture({ visual, tone, big = false }: { visual: Visual; tone?: "sand" | "violet"; big?: boolean }) {
    if (visual.type === "none") return null;
    const panel = `${p.panel} ${tone === "sand" ? p.sand : ""} ${big ? p.heroPanel : ""}`;
    const short = visual.caption && visual.caption.length <= 56 ? visual.caption : null;
    const long = visual.caption && !short ? <figcaption className={p.caption}>{visual.caption}</figcaption> : null;
    if (visual.type === "gallery") {
        return (
            <div className={p.gallery}>
                {GALLERY.map(app => <AppWindow key={app.title} picture={app.picture} title={app.title} who={app.who} sizes="(max-width: 860px) 100vw, 560px" />)}
                {long ?? (short && <p className={p.caption}>{short}</p>)}
            </div>
        );
    }
    if (visual.type === "vignette") {
        const Moment = VIGNETTES[visual.id];
        if (!Moment) return null;
        return (
            <figure className={panel} data-rise="">
                {short && <Note>{short}</Note>}
                <div className={big ? p.bigMoment : p.moment}><Moment /></div>
                {long}
            </figure>
        );
    }
    if (big && visual.window) {
        return (
            <div className={p.heroShot}>
                <AppWindow picture={visual.picture} title={visual.window} who={visual.who} sizes="(max-width: 1240px) 100vw, 1200px" />
                {visual.caption && <p className={p.shown}>{visual.who && <img src={FACES[visual.who]} alt="" width={28} height={28} />}{visual.caption}</p>}
            </div>
        );
    }
    return (
        <figure className={panel} data-rise="">
            {short && <Note>{short}</Note>}
            <div className={p.card} style={visual.maxWidth ? { maxWidth: visual.maxWidth, marginInline: "auto" } : undefined}>
                <Shot picture={visual.picture} sizes={big ? "(max-width: 1240px) 100vw, 1100px" : "(max-width: 1080px) 100vw, 680px"} />
            </div>
            {long}
        </figure>
    );
}
