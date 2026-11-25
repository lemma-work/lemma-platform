import Link from "next/link";
import type { ReactNode } from "react";

import {
    BUILT,
    carriedAccounts,
    DAY_SECTIONS,
    dayWriters,
    getBuilt,
    getPlugin,
    installHref,
    lineName,
    listed,
    makerOf,
    nameOf,
    TABLES,
    tiesOf,
    type BuiltPlugin,
    type DayItem,
    type PlannedPlugin,
} from "../catalog";
import { faceSrc } from "../faces";
import { demoNote } from "../page/plugin-page";
import { TRY_HASH } from "../page/try-hash";
import { Visual } from "../page/visual";
import { Shot } from "../shot";
import { DevFrame } from "./dev-frame";
import { PluginHero } from "./plugin-hero";
import { Row } from "./store";
import p from "./plugin.module.css";
import s from "./store.module.css";

/* A plugin's page in the docs' and the guide's look: the hero with the real
   app (or its running demo) in a window, then the guide's labelled rows.
   A plugin whose `listing.json` has moments (Mail, so far) gets its steps,
   moments, trust lines and questions; the others are drawn from what their
   listing says they do, their set-up, their pictures and their ties. Every
   word about a plugin is its own, from the generated catalog. */

function tableWords(keys: readonly string[]): string {
    return listed(keys.map((k) => (TABLES[k]?.name ?? k).toLowerCase()));
}

/** Another plugin, linking to its page: its face, name and a line about it. */
function Mini({ slug, line }: { slug: string; line: string }) {
    return (
        <Link className={p.mini} href={`/templates/${slug}`}>
            <img src={faceSrc(slug)} alt="" width={32} height={32} />
            <span className={p.miniName}>{nameOf(slug)}</span>
            <span className={p.miniLine}>{line}</span>
        </Link>
    );
}

/** A note as written, with its `code` set as code. */
function Quoted({ text }: { text: string }) {
    const parts = text.split(/(`[^`]+`)/g);
    return (
        <blockquote className={p.quote}>
            {parts.map((part, i) => (part.startsWith("`") && part.endsWith("`") ? <code key={i}>{part.slice(1, -1)}</code> : part))}
        </blockquote>
    );
}

/** A window of the app, as the hero frames it, for a picture further down. */
function Pane({ title, children, caption, note }: { title: string; children: ReactNode; caption?: string; note?: string }) {
    return (
        <figure className={p.figure}>
            <div className={s.window}>
                <span className={s.windowBar} aria-hidden="true">
                    <i />
                    <i />
                    <i />
                    <span>{title}</span>
                </span>
                {children}
            </div>
            {(caption || note) && (
                <figcaption className={p.caption}>
                    {note && <span className={p.note}>{note}</span>}
                    {caption}
                </figcaption>
            )}
        </figure>
    );
}

/** What a plugin puts on each person's Today, grouped the way Today groups it. */
function DayCard({ items }: { items: Array<DayItem & { from?: BuiltPlugin }> }) {
    const groups = DAY_SECTIONS.map((section) => ({ ...section, items: items.filter((i) => i.verb === section.verb) })).filter((g) => g.items.length);
    return (
        <div className={p.day}>
            <p className={p.dayHead}>
                <img src={faceSrc("today")} alt="" width={22} height={22} />
                Today
            </p>
            {groups.map((group) => (
                <div key={group.verb} className={p.dayGroup}>
                    <p className={p.dayName}>{group.name}</p>
                    <ul>
                        {group.items.map((item) => (
                            <li key={`${item.from?.slug ?? ""}${item.line}`}>
                                {item.from && <img src={faceSrc(item.from.slug)} alt={item.from.name} width={18} height={18} />}
                                {item.line}
                            </li>
                        ))}
                    </ul>
                </div>
            ))}
        </div>
    );
}

/** The built plugins either side of this one, and the way back to all of them. */
function Around({ plugin }: { plugin: BuiltPlugin }) {
    const at = BUILT.findIndex((b) => b.slug === plugin.slug);
    const before = BUILT[at - 1];
    const after = BUILT[at + 1];
    return (
        <nav className={p.around} aria-label="More plugins">
            {before ? (
                <Link href={`/templates/${before.slug}`} className={p.aroundPrev}>
                    <span className={p.aroundK}>previous</span>
                    {before.name}
                </Link>
            ) : (
                <span />
            )}
            <Link href="/templates" className={p.aroundAll}>
                All plugins
            </Link>
            {after ? (
                <Link href={`/templates/${after.slug}`} className={p.aroundNext}>
                    <span className={p.aroundK}>next</span>
                    {after.name}
                </Link>
            ) : (
                <span />
            )}
        </nav>
    );
}

/** The close: the guide's statement band, with the two ways in. */
function Close({ line, demo, install }: { line: string; demo: boolean; install: string | null }) {
    return (
        <section className={s.band} aria-label="Start">
            <div className={s.bandIn}>
                <p className={s.lab}>start</p>
                <div className={s.body}>
                    <p className={s.big}>{line}</p>
                    <p className={p.closeActs}>
                        {demo && (
                            <a className={p.closePrimary} href={TRY_HASH}>
                                Try it here
                            </a>
                        )}
                        {install ? (
                            <Link className={s.bandLink} href={install}>
                                Add to a Lemma
                            </Link>
                        ) : (
                            <span className={p.closeSoon}>You can add it soon.</span>
                        )}
                    </p>
                </div>
            </div>
        </section>
    );
}

export function DevPluginPage({ plugin }: { plugin: BuiltPlugin }) {
    const landing = plugin.landing;
    const install = installHref(plugin);
    const demo = plugin.demo ? { src: plugin.demo, note: demoNote(plugin) } : null;
    const first = plugin.pictures[0];
    const ties = tiesOf(plugin);
    const writers = plugin.day === "reads" ? dayWriters() : [];
    const carried = carriedAccounts(plugin).filter(({ account }) => !account.optional);
    const reads = new Map<string, string[]>();
    for (const table of plugin.reads) {
        const maker = makerOf(table);
        if (maker && !plugin.carries.includes(maker.slug)) reads.set(maker.slug, [...(reads.get(maker.slug) ?? []), table]);
    }
    const together = landing
        ? landing.betterWith.map((item) => ({ slug: item.slug, line: item.line }))
        : ties.map((tie) => {
              const said: string[] = [];
              if (tie.buildsOnThis) said.push(`Adding ${tie.plugin.name} adds ${plugin.name} too.`);
              if (tie.uses.length) said.push(`${plugin.name} reads its ${tableWords(tie.uses)}.`);
              if (tie.usedFor.length) said.push(`It reads ${plugin.name}’s ${tableWords(tie.usedFor)}.`);
              return { slug: tie.plugin.slug, line: said.join(" ") };
          });

    return (
        <DevFrame after={<Close line={landing?.closing ?? `${plugin.job}.`} demo={Boolean(demo)} install={install} />}>
            <PluginHero
                name={plugin.name}
                face={faceSrc(plugin.slug)}
                crumb={plugin.name}
                headline={landing?.hero.headline ?? plugin.job}
                sub={landing?.hero.sub ?? plugin.intro}
                install={install}
                picture={
                    landing ? (
                        <Visual visual={landing.hero.visual} sizes="(max-width: 1180px) 100vw, 1100px" />
                    ) : (
                        <Shot picture={first} alt={`${plugin.name}: ${first.caption}`} sizes="(max-width: 1180px) 100vw, 1100px" eager />
                    )
                }
                caption={landing ? "The real app, on made-up sample data." : `${first.caption}. The real app, on made-up sample data.`}
                demo={demo}
            />

            {landing && (
                <Row label="how it starts">
                    <h2 className={s.h2}>{landing.headings.how.replace(/\.$/, "")}</h2>
                    <ol className={p.setup}>
                        {landing.steps.map((step) => (
                            <li key={step.title}>
                                <b>{step.title}</b>
                                {step.body && <span>{step.body}</span>}
                            </li>
                        ))}
                    </ol>
                </Row>
            )}
            {landing ? (
                landing.moments.map((moment) => (
                    <Row key={moment.title} label={moment.label.toLowerCase()}>
                        <h2 className={s.h2}>{moment.title.replace(/\.$/, "")}</h2>
                        <p className={s.text}>{moment.body}</p>
                        <Pane title={moment.visual.window ? `${plugin.name} · ${moment.visual.window}` : plugin.name} note={moment.note}>
                            <span className={p.closeup}>
                                <Visual visual={moment.visual} sizes="(max-width: 1180px) 100vw, 900px" />
                            </span>
                        </Pane>
                    </Row>
                ))
            ) : (
                <>
                    <Row label="what it does">
                        <h2 className={s.h2}>What it does</h2>
                        <ol className={p.claims}>
                            {plugin.does.map((line) => (
                                <li key={line}>{line}</li>
                            ))}
                        </ol>
                    </Row>
                    {plugin.pictures.length > 1 && (
                        <Row label="the app">
                            <h2 className={s.h2}>The real app, on sample data</h2>
                            {plugin.pictures.slice(1).map((picture) => (
                                <Pane key={picture.light} title={`${plugin.name} · ${picture.caption}`} caption={picture.caption}>
                                    <Shot picture={picture} alt={`${plugin.name}: ${picture.caption}`} sizes="(max-width: 1180px) 100vw, 900px" />
                                </Pane>
                            ))}
                        </Row>
                    )}
                </>
            )}

            {!landing && (
                <Row label="how it starts">
                    <h2 className={s.h2}>Setting it up</h2>
                    {plugin.carries.length > 0 && (
                        <p className={s.text}>
                            {listed(plugin.carries.map(nameOf))} {plugin.carries.length > 1 ? "come" : "comes"} first, each with its own set-up. Then:
                        </p>
                    )}
                    <ol className={p.setup}>
                        {plugin.setup.map((step) => (
                            <li key={step}>
                                <b>{step}</b>
                            </li>
                        ))}
                    </ol>
                </Row>
            )}

            {plugin.day === "writes" && plugin.today.length > 0 && (
                <Row label="on today">
                    <h2 className={s.h2}>What it puts on your Today</h2>
                    <p className={s.text}>
                        <Link className={p.link} href="/templates/today">
                            Today
                        </Link>{" "}
                        gathers what every plugin says needs you, in one place. This is what {plugin.name} adds to it.
                    </p>
                    <DayCard items={plugin.today} />
                </Row>
            )}
            {writers.length > 0 && (
                <Row label="on today">
                    <h2 className={s.h2}>What shows up on it</h2>
                    <p className={s.text}>Each line comes from the plugin it belongs to, and settles there when you deal with it.</p>
                    <DayCard items={writers.flatMap((w) => w.today.map((item) => ({ ...item, from: w })))} />
                </Row>
            )}

            <Row label="what it needs">
                <h2 className={s.h2}>What it asks for</h2>
                {plugin.accounts.length > 0 ? (
                    <ul className={p.accounts}>
                        {plugin.accounts.map((account) => (
                            <li key={account.any.join()}>
                                <b>
                                    {listed(account.any, "or")}
                                    {account.optional && <span className={p.optional}>optional</span>}
                                </b>
                                <span>{account.why}</span>
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className={s.text}>No accounts of its own.</p>
                )}
                {carried.length > 0 && <p className={p.aside}>{carried.map(({ from, account }) => `${from.name} asks each person for ${listed(account.any, "or")}.`).join(" ")}</p>}
                {plugin.carries.length > 0 && (
                    <>
                        <p className={s.text}>Adding it also adds:</p>
                        <div className={p.minis}>
                            {plugin.carries.map((slug) => (
                                <Mini key={slug} slug={slug} line={getBuilt(slug)?.job ?? ""} />
                            ))}
                        </div>
                    </>
                )}
                {[...reads].map(([maker, tables]) => (
                    <p key={maker} className={p.aside}>
                        When {nameOf(maker)} is there too, it reads its {tableWords(tables)}.
                    </p>
                ))}
            </Row>

            {plugin.memory && (
                <Row label="memory">
                    <h2 className={s.h2}>What your Lemma learns</h2>
                    <p className={s.text}>Once it is added, every agent in the Lemma reads this note at the start of each run.</p>
                    <Quoted text={plugin.memory} />
                    {plugin.memoryPersonal && (
                        <>
                            <p className={s.text}>And each person’s own notes say:</p>
                            <Quoted text={plugin.memoryPersonal} />
                        </>
                    )}
                </Row>
            )}

            {together.length > 0 && (
                <Row label="together">
                    <h2 className={s.h2}>{landing?.headings.better.replace(/\.$/, "") ?? "Works with"}</h2>
                    <div className={p.minis}>
                        {together.map((item) => (
                            <Mini key={item.slug} slug={item.slug} line={item.line} />
                        ))}
                    </div>
                </Row>
            )}

            {landing && landing.trust.length > 0 && (
                <Row label="yours stays yours">
                    <h2 className={s.h2}>{landing.headings.trust.replace(/\.$/, "")}</h2>
                    <ul className={p.trust}>
                        {landing.trust.map((item) => (
                            <li key={item.title}>
                                <b>{item.title}</b>
                                <span>{item.body}</span>
                            </li>
                        ))}
                    </ul>
                </Row>
            )}

            {landing && landing.faq.length > 0 && (
                <Row label="questions">
                    <h2 className={s.h2}>{landing.headings.faq.replace(/\.$/, "")}</h2>
                    <dl className={p.faq}>
                        {landing.faq.map((item) => (
                            <div key={item.q}>
                                <dt>{item.q}</dt>
                                <dd>{item.a}</dd>
                            </div>
                        ))}
                    </dl>
                </Row>
            )}

            <Around plugin={plugin} />
        </DevFrame>
    );
}

/** A plugin that is not out yet: what it will do, the work it is for, and
 *  what it builds on. No pictures, since there is nothing to show yet. */
export function DevPlannedPage({ plugin }: { plugin: PlannedPlugin }) {
    const bases = plugin.buildsOn.map((slug) => getPlugin(slug)).filter((x) => x !== undefined);
    return (
        <DevFrame>
            <header className={p.hero}>
                <p className={s.eyebrow}>
                    <Link href="/templates">plugins</Link> <span aria-hidden="true">/</span> coming next
                </p>
                <p className={p.name}>
                    <img src={faceSrc(plugin.slug)} alt="" width={40} height={40} />
                    {plugin.name}
                </p>
                <h1 className={p.title}>{plugin.one}</h1>
                <p className={s.intro}>
                    We are building it for {lineName(plugin.line).toLowerCase()}. It is not ready to add yet.
                </p>
            </header>
            {bases.length > 0 && (
                <Row label="builds on">
                    <h2 className={s.h2}>What it will work with</h2>
                    <div className={p.minis}>
                        {bases.map((base) => (
                            <Mini key={base.slug} slug={base.slug} line={base.kind === "built" ? base.job : base.one} />
                        ))}
                    </div>
                </Row>
            )}
            {plugin.merges.length > 0 && (
                <Row label="also">
                    <h2 className={s.h2}>It also covers</h2>
                    <p className={s.text}>{listed(plugin.merges)}.</p>
                </Row>
            )}
            <Row label="ready now">
                <h2 className={s.h2}>Plugins you can add today</h2>
                <p className={s.text}>
                    <Link className={p.link} href="/templates#shelf">
                        See the plugins that are ready
                    </Link>
                    , each one with a demo you can try in your browser.
                </p>
            </Row>
        </DevFrame>
    );
}
