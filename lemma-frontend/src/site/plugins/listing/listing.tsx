import Link from "next/link";
import { BUILT, byLine, dayReader, listed, nameOf, type BuiltPlugin } from "../catalog";
import { faceSrc } from "../faces";
import { MarketingFrame } from "../page/frame";
import { Shot } from "../shot";
import { stackOf } from "../stack";
import { StackPicture } from "./stack-picture";
import s from "./listing.module.css";

export const LISTING_TITLE = "Ready-made apps that fill themselves in";
export const LISTING_INTRO =
    "Each plugin adds working apps to your Lemma. They read your mail and meetings once, share what they find, and bring you only what needs you.";

/** What adding a plugin brings along, said under its name. */
function bringsLine(plugin: BuiltPlugin): string {
    return plugin.carries.length ? `Adds ${listed(plugin.carries.map(nameOf))} too` : "Works on its own";
}

function Card({ plugin, first, skip }: { plugin: BuiltPlugin; first: boolean; skip: boolean }) {
    /* The plugin whose picture heads the page shows its next one here. */
    const picture = plugin.pictures[skip && plugin.pictures.length > 1 ? 1 : 0];
    return (
        <li>
            <Link className={s.card} href={`/templates/${plugin.slug}`}>
                <span className={s.cardPicture}>
                    <Shot picture={picture} alt={`${plugin.name}: ${picture.caption}`} sizes="(max-width: 760px) 100vw, 620px" eager={first} />
                </span>
                <span className={s.cardText}>
                    <span className={s.cardHead}>
                        <img src={faceSrc(plugin.slug)} alt="" width={36} height={36} />
                        <span className={s.cardName}>{plugin.name}</span>
                    </span>
                    <span className={s.cardJob}>{plugin.job}</span>
                    <span className={s.cardBrings}>{bringsLine(plugin)}</span>
                </span>
            </Link>
        </li>
    );
}

/** `/templates`: the plugins you can add today, first and big; how they
 *  stack; and the ones on the way, by line of work. The page is for someone
 *  choosing a plugin, so it never reads as the build plan: no counts of what
 *  is built, no build order, no positions. */
export function PluginListing() {
    const stack = stackOf();
    const sources = stack.main[stack.main.length - 1]?.nodes.map((n) => nameOf(n.slug)) ?? [];
    const beside = stack.beside.flatMap((row) => row.nodes.map((n) => nameOf(n.slug))).reverse();
    const day = dayReader();
    const lines = byLine().filter((l) => l.planned.length);
    /* The page opens on the plugin that is filled in by all the others. */
    const lead = day ?? BUILT[0];
    const leadPicture = lead.pictures[0];
    return (
        <MarketingFrame>
            <div className={`${s.root} ${s.main}`}>
                <header className={s.hero}>
                    <div>
                        <p className={s.eyebrow}>Plugins</p>
                        <h1 className={s.title}>{LISTING_TITLE}</h1>
                        <p className={s.intro}>{LISTING_INTRO}</p>
                    </div>
                    <figure className={s.heroPicture}>
                        <Link className={s.heroFrame} href={`/templates/${lead.slug}`}>
                            <Shot picture={leadPicture} alt={`${lead.name}: ${leadPicture.caption}`} sizes="(max-width: 960px) 100vw, 680px" eager />
                        </Link>
                        <figcaption>
                            {lead.name}, filled in by the other plugins. Sample data.
                        </figcaption>
                    </figure>
                </header>

                <section className={s.section} aria-labelledby="built-title">
                    <div className={s.sectionHead}>
                        <h2 id="built-title" className={s.h2}>
                            Ready to add
                        </h2>
                        <p className={s.note}>Add any of them to your Lemma today. Every picture is the real app, on sample data.</p>
                    </div>
                    <ul className={s.cards}>
                        {BUILT.map((plugin, i) => (
                            <Card key={plugin.slug} plugin={plugin} first={i < 2} skip={plugin.slug === lead.slug} />
                        ))}
                    </ul>
                </section>

                <section id="stack" className={s.section} aria-labelledby="stack-title">
                    <div className={s.sectionHead}>
                        <p className={s.eyebrow}>How they stack</p>
                        <h2 id="stack-title" className={s.h2}>
                            Read once, used by all of them
                        </h2>
                        <p className={s.lede}>
                            {listed(sources)} read your inbox and calendar. The plugins above build on what they
                            find, and adding one brings what it needs.
                            {day ? ` ${day.name} gathers what all of them say needs you.` : ""}
                            {beside.length ? ` ${listed(beside)} work on their own.` : ""}
                        </p>
                    </div>
                    <StackPicture />
                </section>

                <section id="next" className={s.section} aria-labelledby="next-title">
                    <div className={s.sectionHead}>
                        <p className={s.eyebrow}>Coming soon</p>
                        <h2 id="next-title" className={s.h2}>
                            More on the way
                        </h2>
                        <p className={s.lede}>Grouped by the kind of work they are for.</p>
                    </div>
                    <div className={s.lines}>
                        {lines.map(({ line, planned }) => (
                            <section key={line.id} className={s.line} aria-labelledby={`line-${line.id}`}>
                                <h3 id={`line-${line.id}`} className={s.lineName}>
                                    {line.name}
                                </h3>
                                <ul className={s.lineList}>
                                    {planned.map((p) => (
                                        <li key={p.slug}>
                                            <Link href={`/templates/${p.slug}`}>
                                                <span>
                                                    <span className={s.plannedName}>{p.name}</span>
                                                    <span className={s.plannedOne}>{p.one}</span>
                                                </span>
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            </section>
                        ))}
                    </div>
                </section>
            </div>
        </MarketingFrame>
    );
}
