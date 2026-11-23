import Link from "next/link";
import type { ReactNode } from "react";
import {
    carriedAccounts,
    DAY_SECTIONS,
    dayWriters,
    getBuilt,
    getPlugin,
    installHref,
    jobName,
    lineName,
    listed,
    makerOf,
    nameOf,
    TABLES,
    tiesOf,
    type Account,
    type BuiltPlugin,
    type DayItem,
    type PlannedPlugin,
    type Tie,
} from "../catalog";
import { faceSrc } from "../faces";
import { Shot } from "../shot";
import { Crumbs, Frame, Neighbours } from "./frame";
import { Gallery } from "./gallery";
import { Hero } from "./hero";
import s from "./page.module.css";

function tableWords(keys: readonly string[]): string {
    return listed(keys.map((k) => (TABLES[k]?.name ?? k).toLowerCase()));
}

function Row({ id, title, children }: { id: string; title: string; children: ReactNode }) {
    return (
        <section className={s.row} aria-labelledby={id}>
            <h2 id={id} className={s.rowTitle}>
                {title}
            </h2>
            <div className={s.rowBody}>{children}</div>
        </section>
    );
}

/** A little plugin, linking to its page: its face, name and line. */
function Mini({ slug, line }: { slug: string; line: string }) {
    return (
        <Link className={s.mini} href={`/templates/${slug}`}>
            <img src={faceSrc(slug)} alt="" width={32} height={32} />
            <span>
                <span className={s.miniName}>{nameOf(slug)}</span>
                <span className={s.miniLine}>{line}</span>
            </span>
        </Link>
    );
}

/** The memory note as written, with its `code` set as code. */
function Note({ text }: { text: string }) {
    const parts = text.split(/(`[^`]+`)/g);
    return (
        <blockquote className={s.note}>
            {parts.map((part, i) => (part.startsWith("`") && part.endsWith("`") ? <code key={i}>{part.slice(1, -1)}</code> : part))}
        </blockquote>
    );
}

function AccountLine({ account }: { account: Account }) {
    return (
        <li className={s.account}>
            <span className={s.accountName}>
                {listed(account.any, "or")}
                {account.optional && <span className={s.optional}>Optional</span>}
            </span>
            <span className={s.accountWhy}>{account.why}</span>
        </li>
    );
}

function Asks({ plugin }: { plugin: BuiltPlugin }) {
    const carried = carriedAccounts(plugin)
        .filter(({ account }) => !account.optional)
        .map(({ from, account }) => `${from.name} asks each person for ${listed(account.any, "or")}.`);
    if (!plugin.accounts.length && !carried.length) {
        return <p className={s.plain}>No accounts to connect.</p>;
    }
    return (
        <>
            {plugin.accounts.length ? (
                <ul className={s.accounts}>
                    {plugin.accounts.map((a) => (
                        <AccountLine key={a.any.join()} account={a} />
                    ))}
                </ul>
            ) : (
                <p className={s.plain}>Nothing of its own.</p>
            )}
            {carried.length > 0 && <p className={s.through}>{carried.join(" ")}</p>}
        </>
    );
}

function Brings({ plugin }: { plugin: BuiltPlugin }) {
    const reads = new Map<string, string[]>();
    for (const table of plugin.reads) {
        const maker = makerOf(table);
        if (maker && !plugin.carries.includes(maker.slug)) reads.set(maker.slug, [...(reads.get(maker.slug) ?? []), table]);
    }
    return (
        <>
            {plugin.carries.length ? (
                <>
                    <p className={s.plain}>Adding it also adds {listed(plugin.carries.map(nameOf))}.</p>
                    <div className={s.minis}>
                        {plugin.carries.map((slug) => (
                            <Mini key={slug} slug={slug} line={getBuilt(slug)?.job ?? ""} />
                        ))}
                    </div>
                </>
            ) : (
                <p className={s.plain}>Nothing else. It works on its own.</p>
            )}
            {[...reads].map(([maker, tables]) => (
                <p key={maker} className={s.through}>
                    When {nameOf(maker)} is there too, it reads its {tableWords(tables)}.
                </p>
            ))}
        </>
    );
}

function DayList({ items }: { items: Array<DayItem & { from?: BuiltPlugin }> }) {
    const sections = DAY_SECTIONS.map((section) => ({ ...section, items: items.filter((i) => i.verb === section.verb) })).filter((x) => x.items.length);
    return (
        <dl className={s.day}>
            {sections.map((section) => (
                <div key={section.verb} className={s.dayGroup}>
                    <dt>{section.name}</dt>
                    {section.items.map((item) => (
                        <dd key={`${item.from?.slug ?? ""}${item.line}`}>
                            {item.from && (
                                <Link href={`/templates/${item.from.slug}`} className={s.dayFrom}>
                                    <img src={faceSrc(item.from.slug)} alt="" width={20} height={20} />
                                    {item.from.name}
                                </Link>
                            )}
                            {item.line}
                        </dd>
                    ))}
                </div>
            ))}
        </dl>
    );
}

/** Said under a running demo: that it is made up, and how to move between its
 *  apps when its own bar can switch them (Booking's page as a visitor sees it). */
export function demoNote(plugin: BuiltPlugin): string {
    const views = plugin.demoViews.map((v) => v.label);
    const moving = views.length > 1 ? ` Switch between ${listed(views)} in its top bar.` : "";
    return `${plugin.name} running on made-up sample data. Nothing you do here is saved or sent.${moving}`;
}

function tieSentence(plugin: BuiltPlugin, tie: Tie): string {
    const said: string[] = [];
    if (tie.buildsOnThis) said.push(`Adding ${tie.plugin.name} adds ${plugin.name} too.`);
    if (tie.uses.length) said.push(`${plugin.name} reads its ${tableWords(tie.uses)}.`);
    if (tie.usedFor.length) said.push(`It reads ${plugin.name}’s ${tableWords(tie.usedFor)}.`);
    return said.join(" ");
}

/** A built plugin's page: what it is and does, its real pictures, and the
 *  rest from its data: set-up, accounts, what it brings, what it puts on
 *  Today, what the Lemma learns, and what it works with. */
export function BuiltPluginPage({ plugin }: { plugin: BuiltPlugin }) {
    const install = installHref(plugin);
    const ties = tiesOf(plugin);
    const writers = plugin.day === "reads" ? dayWriters() : [];
    const first = plugin.pictures[0];
    return (
        <Frame>
            <Hero
                name={plugin.name}
                words={
                    <>
                        <Crumbs name={plugin.name} />
                        <h1 className={s.heading}>
                            <span className={s.nameLine}>
                                <img src={faceSrc(plugin.slug)} alt="" width={48} height={48} />
                                <span className={s.name}>{plugin.name}</span>
                            </span>
                            <span className={s.job}>{plugin.job}</span>
                        </h1>
                        <p className={s.intro}>{plugin.intro}</p>
                    </>
                }
                soon={
                    install ? (
                        <Link className={s.add} href={install}>
                            Add to a Lemma
                        </Link>
                    ) : (
                        <>
                            <span className={s.soon}>Coming soon</span>
                            <span className={s.soonWhy}>You will be able to add it from here soon.</span>
                        </>
                    )
                }
                after={plugin.jobs.length > 0 && <p className={s.forJobs}>For {listed(plugin.jobs.map(jobName))}</p>}
                poster={<Shot picture={first} alt={`${plugin.name}: ${first.caption}`} sizes="(max-width: 900px) 100vw, 660px" eager />}
                caption={`${first.caption}, on made-up sample data.`}
                demo={plugin.demo ? { src: plugin.demo, note: demoNote(plugin) } : null}
            />

            <section className={s.app} aria-label={`Pictures of ${plugin.name}`}>
                <Gallery
                    name={plugin.name}
                    captions={plugin.pictures.map((p) => p.caption)}
                    pictures={plugin.pictures.map((picture) => (
                        <Shot key={picture.light} picture={picture} alt={`${plugin.name}: ${picture.caption}`} sizes="(max-width: 1240px) 100vw, 1180px" />
                    ))}
                />
                <p className={s.sample}>Pictures of the real app on made-up sample data.</p>
            </section>

            <div className={s.rows}>
                <Row id="does" title="What it does">
                    <ul className={s.does}>
                        {plugin.does.map((line) => (
                            <li key={line}>{line}</li>
                        ))}
                    </ul>
                </Row>
                <Row id="setup" title="Setting it up">
                    {plugin.carries.length > 0 && (
                        <p className={s.through}>{listed(plugin.carries.map(nameOf))} come first, each with its own set-up. Then:</p>
                    )}
                    <ol className={s.steps}>
                        {plugin.setup.map((step) => (
                            <li key={step}>{step}</li>
                        ))}
                    </ol>
                </Row>
                <Row id="asks" title="What it asks for">
                    <Asks plugin={plugin} />
                </Row>
                <Row id="brings" title="What comes with it">
                    <Brings plugin={plugin} />
                </Row>
                {plugin.day === "writes" && plugin.today.length > 0 && (
                    <Row id="today" title="What it adds to Today">
                        <DayList items={plugin.today} />
                        <p className={s.through}>
                            <Link href="/templates/today">Today</Link> shows each person their own, in one place.
                        </p>
                    </Row>
                )}
                {writers.length > 0 && (
                    <Row id="today" title="What shows up on it">
                        <DayList items={writers.flatMap((w) => w.today.map((item) => ({ ...item, from: w })))} />
                    </Row>
                )}
                {plugin.memory && (
                    <Row id="learns" title="What your Lemma learns">
                        <p className={s.plain}>Once it is added, every agent in your Lemma reads this note at the start of each run:</p>
                        <Note text={plugin.memory} />
                        {plugin.memoryPersonal && (
                            <>
                                <p className={s.plain}>And each person’s own notes say:</p>
                                <Note text={plugin.memoryPersonal} />
                            </>
                        )}
                    </Row>
                )}
                {ties.length > 0 && (
                    <Row id="works" title="Works with">
                        <ul className={s.ties}>
                            {ties.map((tie) => (
                                <li key={tie.plugin.slug}>
                                    <Mini slug={tie.plugin.slug} line={tieSentence(plugin, tie)} />
                                </li>
                            ))}
                        </ul>
                    </Row>
                )}
            </div>

            <Neighbours position={plugin.position} />
        </Frame>
    );
}

/** A plugin that is not out yet: one line, the work it is for, and what it
 *  works with. No pictures, since there is nothing to show, and no place in
 *  the build order, which is ours, not the reader's. */
export function PlannedPluginPage({ plugin }: { plugin: PlannedPlugin }) {
    const bases = plugin.buildsOn.map((slug) => getPlugin(slug)).filter((p) => p !== undefined);
    return (
        <Frame>
            <header className={s.heroPlain}>
                <Crumbs name={plugin.name} />
                <h1 className={s.heading}>
                    <span className={s.nameLine}>
                        <img src={faceSrc(plugin.slug)} alt="" width={48} height={48} />
                        <span className={s.name}>{plugin.name}</span>
                    </span>
                    <span className={s.job}>{plugin.one}</span>
                </h1>
                <p className={s.order}>
                    Coming soon, in {lineName(plugin.line)}.
                </p>
            </header>
            <div className={s.rows}>
                <Row id="builds-on" title="What it works with">
                    {bases.length ? (
                        <div className={s.minis}>
                            {bases.map((base) => (
                                <Mini key={base.slug} slug={base.slug} line={base.kind === "built" ? base.job : base.one} />
                            ))}
                        </div>
                    ) : (
                        <p className={s.plain}>Nothing else. It works on its own.</p>
                    )}
                </Row>
                {plugin.merges.length > 0 && (
                    <Row id="covers" title="It also covers">
                        <p className={s.plain}>{listed(plugin.merges)}.</p>
                    </Row>
                )}
            </div>
            <Neighbours position={plugin.position} />
        </Frame>
    );
}
