import Link from "next/link";
import m from "@/app/(marketing)/landing.module.css";
import { Note } from "@/app/(marketing)/pictures";
import p from "@/app/(marketing)/product.module.css";
import { installHref, nameOf, type BuiltPlugin, type Landing, type Visual as VisualData } from "../catalog";
import { faceSrc } from "../faces";
import { MarketingFrame } from "./frame";
import { LandingHero } from "./landing-hero";
import { demoNote } from "./plugin-page";
import { PluginWindow } from "./plugin-window";
import { TRY_HASH } from "./try-hash";
import { Visual } from "./visual";
import l from "./landing.module.css";

/* A plugin's landing page, for a plugin whose `listing.json` has moments
   (the rest keep `BuiltPluginPage`). It is a product page in everything but
   its words: the same frame, the centred hero over the app's window, how it
   works in three steps, each claim beside its proof on a tinted panel with a
   note in the margin, the trust columns, and the violet close. Every word is
   from the listing and every picture a capture of the app on its sample
   (`_kit/bin/moments.mjs`), through the generated catalog. */

/** A moment's proof: the capture in the plugin's window on a tinted panel,
 *  with the claim's one detail written beside it by hand. A crop that cuts
 *  through something on purpose runs off the panel's foot. */
function Proof({ plugin, visual, note, tone }: { plugin: BuiltPlugin; visual: VisualData; note: string; tone: "violet" | "sand" }) {
    const phoneBleed = visual.phone ? visual.phone.bleed : visual.bleed;
    return (
        <figure
            className={`${p.panel} ${l.panel} ${tone === "sand" ? `${p.sand} ${l.sand}` : ""}`}
            data-rise=""
            data-bleed={visual.bleed ?? undefined}
            data-phone-bleed={phoneBleed ?? undefined}
        >
            <Note>{note}</Note>
            <PluginWindow slug={plugin.slug} title={visual.window ? `${plugin.name} · ${visual.window}` : plugin.name} fit>
                <Visual visual={visual} sizes="(max-width: 1080px) 100vw, 640px" />
            </PluginWindow>
        </figure>
    );
}

export function LandingPluginPage({ plugin, landing }: { plugin: BuiltPlugin; landing: Landing }) {
    const install = installHref(plugin);
    const demo = plugin.demo ? { src: plugin.demo, note: demoNote(plugin) } : null;
    const { hero, headings } = landing;
    return (
        <MarketingFrame>
            <LandingHero
                name={plugin.name}
                slug={plugin.slug}
                label={hero.eyebrow}
                headline={hero.headline}
                sub={hero.sub}
                install={install}
                visual={hero.visual}
                caption="The real app, on made-up sample data."
                face={faceSrc(plugin.slug)}
                demo={demo}
            />

            <section className={`${m.wrap} ${m.section}`} aria-labelledby="how-title">
                <p className={m.label}>How it works</p>
                <h2 id="how-title" className={m.title}>
                    {headings.how}
                </h2>
                <ol className={m.steps}>
                    {landing.steps.map((step, index) => (
                        <li key={step.title} className={`${m.step} ${l.step}`} data-rise="">
                            <div className={l.stepText}>
                                <span className={m.stepNumber} aria-hidden="true">
                                    {index + 1}
                                </span>
                                <h3 className={m.stepTitle}>{step.title}</h3>
                                {step.body && <p className={m.stepBody}>{step.body}</p>}
                            </div>
                            <figure
                                className={`${p.panel} ${l.panel} ${l.stepPanel}`}
                                data-bleed={step.visual.bleed ?? undefined}
                                data-phone-bleed={step.visual.bleed ?? undefined}
                            >
                                <div className={p.card}>
                                    <Visual visual={step.visual} sizes="(max-width: 1080px) 100vw, 380px" />
                                </div>
                            </figure>
                        </li>
                    ))}
                </ol>
            </section>

            {landing.moments.map((moment, index) => (
                <section
                    key={moment.title}
                    className={`${m.wrap} ${m.section} ${p.proof} ${index % 2 ? p.flip : ""}`}
                    aria-labelledby={`moment-${index}`}
                >
                    <div className={p.words} data-rise="">
                        <p className={m.label}>{moment.label}</p>
                        <h2 id={`moment-${index}`} className={p.proofTitle}>
                            {moment.title}
                        </h2>
                        <p className={p.proofBody}>{moment.body}</p>
                    </div>
                    <div className={p.picture}>
                        <Proof plugin={plugin} visual={moment.visual} note={moment.note} tone={index % 2 ? "sand" : "violet"} />
                    </div>
                </section>
            ))}

            {landing.betterWith.length > 0 && (
                <section className={`${m.wrap} ${m.section}`} aria-labelledby="better-title">
                    <p className={m.label}>Better with</p>
                    <h2 id="better-title" className={m.title}>
                        {headings.better}
                    </h2>
                    <div className={`${m.trust} ${l.better}`}>
                        {landing.betterWith.map((item) => (
                            <div key={item.slug} data-rise="">
                                <Link href={`/templates/${item.slug}`}>
                                    <img src={faceSrc(item.slug, false)} alt="" width={56} height={56} />
                                    <h3>{nameOf(item.slug)}</h3>
                                    <p>{item.line}</p>
                                </Link>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            <section className={`${m.wrap} ${m.section}`} aria-labelledby="trust-title">
                <p className={m.label}>Yours stays yours</p>
                <h2 id="trust-title" className={m.title}>
                    {headings.trust}
                </h2>
                <div className={m.trust}>
                    {landing.trust.map((item) => (
                        <div key={item.title} data-rise="">
                            <h3>{item.title}</h3>
                            <p>{item.body}</p>
                        </div>
                    ))}
                </div>
            </section>

            <section className={`${m.wrap} ${m.section} ${p.saying}`} aria-labelledby="questions-title">
                <div data-rise="">
                    <p className={m.label}>Questions</p>
                    <h2 id="questions-title" className={m.title}>
                        {headings.faq}
                    </h2>
                </div>
                <ul className={p.points}>
                    {landing.faq.map((item) => (
                        <li key={item.q} data-rise="">
                            <b>{item.q}</b>
                            <span>{item.a}</span>
                        </li>
                    ))}
                </ul>
            </section>

            <section className={p.close} data-rise="" aria-labelledby="close-title">
                <img className={l.closeFace} src={faceSrc(plugin.slug, false)} alt="" width={96} height={96} />
                <h2 id="close-title">{landing.closing}</h2>
                {!install && <p className={p.closeSub}>You will be able to add it to a Lemma soon.</p>}
                <div className={`${m.ctas} ${p.centred}`}>
                    {demo && (
                        <a className={p.closePrimary} href={TRY_HASH}>
                            Try it live
                        </a>
                    )}
                    {install ? (
                        <Link className={p.closeGhost} href={install}>
                            Add to a Lemma
                        </Link>
                    ) : (
                        <span className={p.closeGhost}>Coming soon</span>
                    )}
                </div>
            </section>
        </MarketingFrame>
    );
}
