import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

import { BUILT, byLine, jobName, listed, nameOf, type BuiltPlugin } from "../catalog";
import { faceSrc } from "../faces";
import { TRY_HASH } from "../page/try-hash";
import { DevFrame } from "./dev-frame";
import { ShelfFilter } from "./shelf-filter";
import s from "./store.module.css";

export const STORE_TITLE = "Add a plugin, and the work starts";
export const STORE_INTRO =
    "Each plugin gives your Lemma finished apps that run on your own mail and calendar. Mail drafts your replies. Meetings briefs you before each call. Deal tracker moves deals as the conversation does.";

/** The part of each plugin's real capture its card shows: the file (in light,
 *  with a `-dark` twin) and the window into it, as fractions of the picture,
 *  `w` wide, at the card's 4:3. Chosen to be readable at card size: the reply
 *  being drafted, the board of deals, the slide being edited. */
const CROPS: Record<string, { file: string; x: number; y: number; w: number }> = {
    mail: { file: "mail/moments/drafted", x: 0, y: 0, w: 1 },
    meetings: { file: "meetings/meetings-brief", x: 0.2, y: 0.04, w: 0.4 },
    today: { file: "today/today", x: 0.07, y: 0.01, w: 0.42 },
    "people-map": { file: "people-map/person", x: 0.02, y: 0.03, w: 0.42 },
    tasks: { file: "tasks/tasks-board", x: 0.19, y: 0.07, w: 0.42 },
    "deal-tracker": { file: "deal-tracker/board", x: 0.01, y: 0.06, w: 0.45 },
    "booking-page": { file: "booking-page/booking-time", x: 0.15, y: 0.05, w: 0.5 },
    "lead-research": { file: "lead-research/lead", x: 0, y: 0.02, w: 0.42 },
    "brand-kit": { file: "brand-kit/brand-design", x: 0.06, y: 0.05, w: 0.5 },
    "deck-maker": { file: "deck-maker/decks-editor", x: 0.15, y: 0.26, w: 0.52 },
};

/** The order the job row lists them in: the jobs most plugins are for first. */
const JOB_ORDER = ["SALES", "FOUNDER", "MARKETING", "OPERATIONS", "SUPPORT", "PROJECT_MANAGEMENT", "ENGINEERING", "PRODUCT", "HR"];

/** What happens once a plugin is added, in the order it happens. */
const STEPS: Array<{ title: string; body: string }> = [
    { title: "Add it to a Lemma.", body: "You see everything it will create before anything changes." },
    { title: "Connect your own accounts.", body: "Each person links their own mail or calendar, where the plugin needs one." },
    { title: "Open apps already filled in.", body: "It works through what is already there and keeps up with what arrives. Anything that matters waits for your yes." },
];

/** A window into a plugin's real capture, in light and dark. */
export function Crop({ slug, alt, eager = false }: { slug: string; alt: string; eager?: boolean }) {
    const crop = CROPS[slug];
    if (!crop) return null;
    const style = { "--x": crop.x, "--y": crop.y, "--w": crop.w } as CSSProperties;
    const loading = eager ? "eager" : "lazy";
    return (
        <span className={s.crop} style={style}>
            <img className={s.light} src={`/plugins/${crop.file}-light.webp`} alt={alt} loading={loading} />
            <img className={s.dark} src={`/plugins/${crop.file}-dark.webp`} alt={alt} loading={loading} />
        </span>
    );
}

/** One of the guide's labelled rows: a label at the left, the content beside it. */
export function Row({ label, id, children }: { label: string; id?: string; children: ReactNode }) {
    return (
        <section className={s.row} id={id} aria-label={label}>
            <p className={s.lab}>{label}</p>
            <div className={s.body}>{children}</div>
        </section>
    );
}

function Card({ plugin, first }: { plugin: BuiltPlugin; first: boolean }) {
    return (
        <li className={s.card} data-jobs={plugin.jobs.join(" ")}>
            <Link className={s.cardPicture} href={`/templates/${plugin.slug}`} tabIndex={-1} aria-hidden="true">
                <Crop slug={plugin.slug} alt="" eager={first} />
            </Link>
            <div className={s.cardBody}>
                <p className={s.cardHead}>
                    <img src={faceSrc(plugin.slug)} alt="" width={28} height={28} />
                    <Link href={`/templates/${plugin.slug}`}>{plugin.name}</Link>
                </p>
                <p className={s.cardJob}>{plugin.job}.</p>
                {plugin.carries.length > 0 && <p className={s.cardBrings}>Adds {listed(plugin.carries.map(nameOf))}</p>}
                <p className={s.cardActs}>
                    {plugin.demo && (
                        <Link className={s.try} href={`/templates/${plugin.slug}${TRY_HASH}`}>
                            Try it
                        </Link>
                    )}
                    <Link className={s.more} href={`/templates/${plugin.slug}`}>
                        How it works
                    </Link>
                </p>
            </div>
        </li>
    );
}

/** `/templates`, in the docs' and the guide's look: the docs' header and
 *  footer, the guide's labelled rows, thin Jost headings, Newsreader for the
 *  words, Lemma's violet. One promise beside the real app; what happens once
 *  a plugin is added; the plugins that bring others; every plugin, narrowed
 *  by the work you do; what comes next; how to make your own. It never reads
 *  as the build plan: no counts of what is built, no build order. */
export function PluginStore() {
    const mail = BUILT.find((p) => p.slug === "mail") ?? BUILT[0];
    const sets = BUILT.filter((p) => p.carries.length >= 2).sort((a, b) => b.carries.length - a.carries.length);
    const jobs = JOB_ORDER.map((id) => ({ id, name: jobName(id), count: BUILT.filter((p) => p.jobs.includes(id)).length })).filter((j) => j.count > 0);
    const coming = byLine().filter((l) => l.planned.length);
    return (
        <DevFrame after={<Band />}>
            <header className={s.hero}>
                <div className={s.heroText}>
                    <p className={s.eyebrow}>plugins</p>
                    <h1 className={s.title}>{STORE_TITLE}</h1>
                    <p className={s.intro}>{STORE_INTRO}</p>
                    <p className={s.heroActs}>
                        <Link className={s.primary} href={`/templates/${mail.slug}${TRY_HASH}`}>
                            Try {mail.name} now
                        </Link>
                    </p>
                    <p className={s.heroNote}>It runs in your browser on sample mail. No account, and nothing is sent.</p>
                </div>
                <figure className={s.heroPicture}>
                    <span className={s.window}>
                        <span className={s.windowBar} aria-hidden="true">
                            <i />
                            <i />
                            <i />
                            <span>{mail.name} · Needs reply</span>
                        </span>
                        <Crop slug="mail" alt={`${mail.name} with a reply already drafted, in the sender's voice`} eager />
                    </span>
                    <figcaption>{mail.name}, with the reply written before the conversation is opened. Sample data.</figcaption>
                </figure>
            </header>

            <Row label="how it works">
                <h2 className={s.h2}>Three steps, then it runs</h2>
                <ol className={s.steps}>
                    {STEPS.map((step, i) => (
                        <li key={step.title}>
                            <span className={s.stepN}>{i + 1}</span>
                            <b>{step.title}</b>
                            <span>{step.body}</span>
                        </li>
                    ))}
                </ol>
            </Row>

            <Row label="together">
                <h2 className={s.h2}>Plugins share what they read</h2>
                <p className={s.text}>Mail and Meetings read your inbox and calendar once. The plugins built on them use that reading, so adding one brings the ones it needs.</p>
                <ul className={s.sets}>
                    {sets.map((plugin) => (
                        <li key={plugin.slug}>
                            <Link className={s.set} href={`/templates/${plugin.slug}`}>
                                <span className={s.setFaces} aria-hidden="true">
                                    <img className={s.setLead} src={faceSrc(plugin.slug)} alt="" width={44} height={44} />
                                    <span className={s.setPlus}>+</span>
                                    {plugin.carries.map((slug) => (
                                        <img key={slug} src={faceSrc(slug)} alt="" width={28} height={28} />
                                    ))}
                                </span>
                                <span className={s.setName}>{plugin.name}</span>
                                <span className={s.setBrings}>Adds {listed(plugin.carries.map(nameOf))}</span>
                            </Link>
                        </li>
                    ))}
                </ul>
            </Row>

            <Row label="ready now" id="shelf">
                <h2 className={s.h2}>Pick by the work you do</h2>
                <ShelfFilter jobs={jobs}>
                    {BUILT.map((plugin, i) => (
                        <Card key={plugin.slug} plugin={plugin} first={i < 2} />
                    ))}
                </ShelfFilter>
            </Row>

            <Row label="coming next">
                <h2 className={s.h2}>What we are building next</h2>
                <p className={s.text}>Grouped by the kind of work. Open a line to see what each one will do.</p>
                <div className={s.soon}>
                    {coming.map(({ line, planned }) => (
                        <details key={line.id} className={s.soonLine}>
                            <summary>
                                <span className={s.soonName}>{line.name}</span>
                                <span className={s.soonSome}>
                                    {planned.length > 3
                                        ? `${planned.slice(0, 3).map((p) => p.name).join(", ")} and more`
                                        : listed(planned.map((p) => p.name))}
                                </span>
                            </summary>
                            <ul>
                                {planned.map((p) => (
                                    <li key={p.slug}>
                                        <Link className={s.soonItem} href={`/templates/${p.slug}`}>
                                            {p.name}
                                        </Link>
                                        <span className={s.soonOne}>{p.one}</span>
                                    </li>
                                ))}
                            </ul>
                        </details>
                    ))}
                </div>
            </Row>
        </DevFrame>
    );
}

/** The close: the guide's statement band, on making your own. */
function Band() {
    return (
        <section className={s.band} aria-label="Make your own">
            <div className={s.bandIn}>
                <p className={s.lab}>make your own</p>
                <div className={s.body}>
                    <p className={s.big}>A plugin is a Lemma, packaged.</p>
                    <p className={s.bandText}>Its apps, tables, agents and set-up travel as one bundle. Package the way your team works, and anyone can add it.</p>
                    <Link className={s.bandLink} href="/docs">
                        How a plugin is built
                    </Link>
                </div>
            </div>
        </section>
    );
}
