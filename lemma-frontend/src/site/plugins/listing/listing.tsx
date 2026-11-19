import Link from "next/link";
import { BUILT, byLine, dayReader, listed, nameOf, PLANNED, type BuiltPlugin } from "../catalog";
import { faceSrc } from "../faces";
import { MarketingFrame } from "../page/frame";
import { Shot } from "../shot";
import { stackOf } from "../stack";
import { StackPicture } from "./stack-picture";
import s from "./listing.module.css";

export const LISTING_TITLE = "Ready-made apps that fill themselves in";
export const LISTING_INTRO =
    "Each plugin adds working apps to your Lemma. Most build on your mail and meetings, read once and shared between them, and bring you only what needs you.";

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

/** `/templates`: the built plugins first and big, how they stack, and the
 *  rest of the catalog in the order it will be built. */
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
                        <p className={s.intro}>
                            {LISTING_INTRO} So far, {BUILT.length} are built.
                        </p>
                    </div>
                    <figure className={s.heroPicture}>
                        <Link className={s.heroFrame} href={`/templates/${lead.slug}`}>
                            <Shot picture={leadPicture} alt={`${lead.name}: ${leadPicture.caption}`} sizes="(max-width: 960px) 100vw, 680px" eager />
                        </Link>
                        <figcaption>
                            {lead.name}, filled in by the other plugins, on made-up sample data.
                        </figcaption>
                    </figure>
                </header>

                <section className={s.section} aria-labelledby="built-title">
                    <div className={s.sectionHead}>
                        <h2 id="built-title" className={s.h2}>
                            Built and tested
                        </h2>
                        <p className={s.note}>Every picture is the real app, on made-up sample data.</p>
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
                            Read once, used by everything above
                        </h2>
                        <p className={s.lede}>
                            {listed(sources)} sit underneath and read your inbox and calendar. The plugins above
                            build on what they found, and adding one brings what it stands on.
                            {day ? ` ${day.name} reads what all of them say needs you.` : ""}
                            {beside.length ? ` ${listed(beside)} stand beside: they need neither.` : ""}
                        </p>
                    </div>
                    <StackPicture />
                </section>

                <section id="next" className={s.section} aria-labelledby="next-title">
                    <div className={s.sectionHead}>
                        <p className={s.eyebrow}>Coming next</p>
                        <h2 id="next-title" className={s.h2}>
                            {PLANNED.length} more, in the order they will be built
                        </h2>
                        <p className={s.lede}>
                            Grouped by the line of work they are for, and numbered in the order they will be built.
                        </p>
                    </div>
                    <div className={s.lines}>
                        {lines.map(({ line, built, planned }) => (
                            <section key={line.id} className={s.line} aria-labelledby={`line-${line.id}`}>
                                <h3 id={`line-${line.id}`} className={s.lineName}>
                                    {line.name}
                                    {built.length > 0 && (
                                        <small>
                                            after {built.length === 1 ? built[0].name : `the ${built.length} above`}
                                        </small>
                                    )}
                                </h3>
                                <ol className={s.lineList}>
                                    {planned.map((p) => (
                                        <li key={p.slug}>
                                            <Link href={`/templates/${p.slug}`}>
                                                <span className={s.position}>{p.position}</span>
                                                <span>
                                                    <span className={s.plannedName}>{p.name}</span>
                                                    <span className={s.plannedOne}>{p.one}</span>
                                                </span>
                                            </Link>
                                        </li>
                                    ))}
                                </ol>
                            </section>
                        ))}
                    </div>
                </section>
            </div>
        </MarketingFrame>
    );
}
