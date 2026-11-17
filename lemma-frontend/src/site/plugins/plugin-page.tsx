import Link from "next/link";
import { ArrowLeft, ArrowUpRight, Lock } from "@phosphor-icons/react/dist/ssr";
import { SiteFooter, SiteHeader } from "@/site/chrome";
import { PUBLIC_TEMPLATES, templateRunHref } from "@/site/templates/catalog";
import {
    STAGE_NAME,
    gapsOf,
    getPlugin,
    installHref,
    linksOf,
    list,
    makersOf,
    partsOf,
    stageOf,
    usersOf,
} from "./catalog";
import { AppCover } from "./cover";
import { SHARED_TABLES, jobName } from "./tables";
import type {
    AccountNeed,
    BundleParts,
    ConnectorPart,
    Plugin,
    Stage,
    TableKind,
} from "./types";
import s from "./plugins.module.css";

const LINK_STATE = {
    works: "Works with it",
    planned: "Not built yet",
    clashes: "Clashes today",
} as const;

const STAGE_NOTE: Record<Stage, string> = {
    ready: "You'll see everything it adds before anything changes.",
    runs: "It runs as a Lemma today but isn't published where the install can read it.",
    partly: "Part of it is built. Where it stands is below.",
    planned:
        "Drawn, not built. This page shows what it would make, use and ask for.",
};

/** A first template the plugin can start from, when it can't install itself. */
function startFrom(plugin: Plugin) {
    for (const b of plugin.basis ?? []) {
        const t = PUBLIC_TEMPLATES.find((x) => x.slug === b.template);
        if (t) return { name: t.name, href: templateRunHref(t) };
    }
    return null;
}

/** A plugin's landing page: what it does for a person in the job, the apps it
 *  starts with, the tables it makes and uses, the plugins it works with, the
 *  accounts it asks for, what runs it, and what it still lacks. A plugin with
 *  no bundle yet gets the same page without the parts it doesn't have. */
export function PluginPage({ plugin }: { plugin: Plugin }) {
    const parts = partsOf(plugin);
    const stage = stageOf(plugin);
    const href = installHref(plugin);
    const start = href ? null : startFrom(plugin);
    const links = linksOf(plugin);
    const needs = (plugin.needs ?? []).map((slug) => getPlugin(slug)!);
    const gaps = gapsOf(plugin);

    return (
        <div className="site">
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <SiteHeader />
            <main id="content" className={s.page}>
                <Link href="/templates" className={s.back}>
                    <ArrowLeft size={14} aria-hidden />
                    All plugins
                </Link>

                <div className={s.heroGrid}>
                    <header className={s.hero}>
                        <p className={s.eyebrow}>
                            Plugin · {plugin.jobs.map(jobName).join(" · ")}
                        </p>
                        <h1>{plugin.headline}</h1>
                        <p className={s.lede}>{plugin.lede}</p>
                    </header>

                    <aside id="install" className={s.installCard}>
                        <p className={s.installName}>{plugin.name}</p>
                        <p className={s.installMeta}>
                            {parts ? partsLine(parts) : tablesLine(plugin)}
                        </p>
                        {href ? (
                            <Link className={s.installButton} href={href}>
                                Install {plugin.name}
                                <ArrowUpRight size={14} aria-hidden />
                            </Link>
                        ) : start ? (
                            <Link
                                className={s.installButton}
                                data-state="alt"
                                href={start.href}
                            >
                                Start from {start.name}
                                <ArrowUpRight size={14} aria-hidden />
                            </Link>
                        ) : (
                            <span
                                className={s.installButton}
                                data-state="off"
                                aria-disabled="true"
                            >
                                {stage === "runs"
                                    ? "Not ready to install"
                                    : "Not built yet"}
                            </span>
                        )}
                        <p className={s.installNote}>
                            {start
                                ? `${start.name} installs today and does part of this.`
                                : STAGE_NOTE[stage]}
                        </p>
                        {plugin.private && (
                            <p className={s.privateNote}>
                                <Lock size={13} aria-hidden />
                                Goes in a Lemma only you can see.
                            </p>
                        )}
                        <AccountsShort plugin={plugin} parts={parts} />
                    </aside>
                </div>

                {plugin.does && (
                    <Section label="What it does">
                        <ul className={s.does}>
                            {plugin.does.map((d) => (
                                <li key={d}>{d}</li>
                            ))}
                        </ul>
                    </Section>
                )}

                {!parts && (
                    <Section label="Where it stands">
                        <p className={s.standing}>
                            <span className={s.stage} data-stage={stage}>
                                {STAGE_NAME[stage]}
                            </span>
                            {stage === "runs"
                                ? "It works as a Lemma today. Its parts aren't listed here yet."
                                : stage === "planned"
                                  ? "Nothing of it runs yet."
                                  : "Pieces of it run today."}
                        </p>
                        {plugin.basis && (
                            <ul className={s.basis}>
                                {plugin.basis.map((b) => (
                                    <li key={b.text}>
                                        <Basis basis={b} />
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Section>
                )}

                {parts && (
                    <Section label="The apps it starts with">
                        <ul className={s.apps}>
                            {parts.apps.map((app) => (
                                <li key={app.name} className={s.app}>
                                    <AppCover app={app} />
                                    <div className={s.appText}>
                                        <h3>
                                            {app.title}
                                            {!app.inBundle && (
                                                <span className={s.flag}>
                                                    Beside the bundle
                                                </span>
                                            )}
                                        </h3>
                                        <p>{app.line}</p>
                                        <ul className={s.screens}>
                                            {app.screens.map((sc) => (
                                                <li key={sc}>{sc}</li>
                                            ))}
                                        </ul>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </Section>
                )}

                <Section label="Tables it makes and uses">
                    <div className={s.makesUses}>
                        <TableKinds
                            title="Makes"
                            empty={
                                parts
                                    ? "No shared table. Its tables are its own."
                                    : "No shared table: it works on tables other plugins make."
                            }
                            kinds={plugin.makes}
                            related={(k) => ({
                                label: "Used by",
                                plugins: usersOf(k),
                            })}
                            self={plugin.slug}
                        />
                        <TableKinds
                            title="Uses"
                            empty="Nothing another plugin made."
                            kinds={plugin.uses}
                            related={(k) => ({
                                label: "Made by",
                                plugins: makersOf(k),
                            })}
                            self={plugin.slug}
                        />
                        {plugin.reads && (
                            <TableKinds
                                title="Reads when it's there"
                                empty=""
                                kinds={plugin.reads}
                                related={(k) => ({
                                    label: "Made by",
                                    plugins: makersOf(k),
                                })}
                                self={plugin.slug}
                            />
                        )}
                    </div>
                    {parts && <TableDetails parts={parts} />}
                </Section>

                {(parts || links.length > 0 || needs.length > 0) && (
                    <Section label="Works with">
                        {links.length || needs.length ? (
                            <ul className={s.links}>
                                {needs.map((p) => (
                                    <li key={p.slug}>
                                        <Link href={`/templates/${p.slug}`}>
                                            {p.name}
                                        </Link>
                                        <span
                                            className={s.state}
                                            data-state="needs"
                                        >
                                            Needs it
                                        </span>
                                        <p>{p.headline}.</p>
                                    </li>
                                ))}
                                {links.map((l) => (
                                    <li key={l.slug}>
                                        <Link href={`/templates/${l.slug}`}>
                                            {l.plugin.name}
                                        </Link>
                                        <span
                                            className={s.state}
                                            data-state={l.state}
                                        >
                                            {LINK_STATE[l.state]}
                                        </span>
                                        <p>{l.why}</p>
                                    </li>
                                ))}
                            </ul>
                        ) : (
                            <p className={s.faint}>
                                Stands alone: it makes and uses no table another
                                of these plugins touches.
                            </p>
                        )}
                    </Section>
                )}

                <Section label="Accounts it asks for">
                    {parts ? (
                        <Connectors
                            connectors={parts.connectors}
                            leans={plugin.uses.length > 0}
                        />
                    ) : (
                        <Accounts plugin={plugin} />
                    )}
                </Section>

                {parts && <Runs parts={parts} />}

                <Section label="Still to do before it installs cleanly">
                    <ul className={s.gaps}>
                        {gaps.map((g, i) => (
                            <li key={i} data-kind={g.kind}>
                                {g.text}
                            </li>
                        ))}
                    </ul>
                </Section>
            </main>
            <div className={s.mobileBar}>
                <span>{plugin.name}</span>
                {href ? (
                    <Link href={href}>Install</Link>
                ) : start ? (
                    <Link href={start.href}>Start from {start.name}</Link>
                ) : (
                    <span className={s.faint}>{STAGE_NAME[stage]}</span>
                )}
            </div>
            <SiteFooter />
        </div>
    );
}

function partsLine(parts: BundleParts) {
    const runs =
        parts.agents.length +
        parts.functions.length +
        parts.workflows.length +
        parts.schedules.length;
    return `${count(parts.apps.length, "app")} · ${count(parts.tables.length, "table")} · ${count(runs, "piece")} that run it`;
}

function tablesLine(plugin: Plugin) {
    const uses = plugin.uses.length + (plugin.reads?.length ?? 0);
    return `Makes ${plugin.makes.length ? count(plugin.makes.length, "shared table") : "no shared table"} · uses ${uses || "none"}`;
}

function Section({
    label,
    children,
}: {
    label: string;
    children: React.ReactNode;
}) {
    return (
        <section className={s.section}>
            <h2 className={s.label}>{label}</h2>
            {children}
        </section>
    );
}

function Basis({ basis }: { basis: NonNullable<Plugin["basis"]>[number] }) {
    const plugin = basis.plugin ? getPlugin(basis.plugin) : null;
    const template = basis.template
        ? PUBLIC_TEMPLATES.find((t) => t.slug === basis.template)
        : null;
    return (
        <>
            <span>{basis.text}</span>
            {plugin && (
                <Link href={`/templates/${plugin.slug}`}>
                    {plugin.name}
                </Link>
            )}
            {template && (
                <Link href={templateRunHref(template)}>
                    Install {template.name}
                    <ArrowUpRight size={12} aria-hidden />
                </Link>
            )}
        </>
    );
}

/** At most this many related plugins by name; the rest are counted. */
const NAMED = 4;

function TableKinds({
    title,
    empty,
    kinds,
    related,
    self,
}: {
    title: string;
    empty: string;
    kinds: TableKind[];
    related: (kind: TableKind) => { label: string; plugins: Plugin[] };
    self: string;
}) {
    return (
        <div>
            <h3 className={s.subhead}>{title}</h3>
            {kinds.length ? (
                <ul className={s.kinds}>
                    {kinds.map((k) => {
                        const { label, plugins } = related(k);
                        const others = plugins.filter((p) => p.slug !== self);
                        const named = others.slice(0, NAMED);
                        const rest = others.length - named.length;
                        return (
                            <li key={k}>
                                <span>{SHARED_TABLES[k].name}</span>
                                <small>{SHARED_TABLES[k].line}</small>
                                {others.length > 0 && (
                                    <small className={s.related}>
                                        {label}{" "}
                                        {named.map((p, i) => (
                                            <span key={p.slug}>
                                                {i > 0 &&
                                                    (i === named.length - 1 &&
                                                    !rest
                                                        ? " and "
                                                        : ", ")}
                                                <Link
                                                    href={`/templates/${p.slug}`}
                                                >
                                                    {p.name}
                                                </Link>
                                            </span>
                                        ))}
                                        {rest > 0 && ` and ${rest} more`}
                                    </small>
                                )}
                            </li>
                        );
                    })}
                </ul>
            ) : (
                <p className={s.faint}>{empty}</p>
            )}
        </div>
    );
}

function TableDetails({ parts }: { parts: BundleParts }) {
    return (
        <details className={s.more}>
            <summary>
                All {count(parts.tables.length, "table")} and their key columns
            </summary>
            <table className={s.grid}>
                <thead>
                    <tr>
                        <th scope="col">Table</th>
                        <th scope="col">Key columns</th>
                        <th scope="col">Who sees rows</th>
                    </tr>
                </thead>
                <tbody>
                    {parts.tables.map((t) => (
                        <tr key={t.name}>
                            <th scope="row">
                                <code>{t.name}</code>
                                {t.kind && (
                                    <span className={s.kindTag}>
                                        {SHARED_TABLES[t.kind].name}
                                    </span>
                                )}
                            </th>
                            <td>
                                {t.key.join(", ")}
                                {t.columns > t.key.length && (
                                    <span className={s.faint}>
                                        {" "}
                                        +{t.columns - t.key.length} more
                                    </span>
                                )}
                            </td>
                            <td>
                                {t.eachPersonsOwn
                                    ? "Each person their own"
                                    : "Everyone in the Lemma"}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </details>
    );
}

function who(c: ConnectorPart) {
    return c.mode === "PER_MEMBER"
        ? "Each person connects their own"
        : "One account for everyone";
}

/** "Gmail or Outlook"; "Gmail, Outlook, Intercom or Zendesk". */
const anyOf = (a: AccountNeed) =>
    a.any.length < 3
        ? a.any.join(" or ")
        : a.any.slice(0, -1).join(", ") + " or " + a.any[a.any.length - 1];

function AccountsShort({
    plugin,
    parts,
}: {
    plugin: Plugin;
    parts: BundleParts | null;
}) {
    const leans = plugin.uses.length > 0;
    const connectors = parts
        ? parts.connectors.map((c) => ({
              name: c.service,
              required: c.required,
          }))
        : (plugin.accounts ?? []).map((a) => ({
              name: anyOf(a),
              required: a.required,
          }));
    const needed = connectors.filter((c) => c.required);
    const optional = connectors.filter((c) => !c.required);
    const verb = parts ? "Asks for" : "Would ask for";
    let text: string;
    if (!connectors.length)
        text = leans
            ? "No accounts of its own: it reads what the plugins it uses connected."
            : "Asks for no accounts.";
    else if (!needed.length)
        text = `Needs no account. Can also use ${optional.length > 3 ? `${optional.length} tools you may already have` : list(optional.map((c) => c.name))}.`;
    else
        text =
            `${verb} ${list(needed.map((c) => c.name))}.` +
            (optional.length
                ? ` Can also use ${optional.length > 3 ? `${optional.length} more` : list(optional.map((c) => c.name))}.`
                : "");
    return <p className={s.installAccounts}>{text}</p>;
}

function Connectors({
    connectors,
    leans,
}: {
    connectors: ConnectorPart[];
    leans: boolean;
}) {
    if (!connectors.length)
        return (
            <p className={s.faint}>
                {leans
                    ? "None of its own. It works on what the plugins it uses already connected."
                    : "None. It works on what you give it in the Lemma."}
            </p>
        );
    return (
        <ul className={s.accounts}>
            {connectors.map((c) => (
                <li key={c.service}>
                    <span className={s.service}>{c.service}</span>
                    <span className={s.accountMeta}>
                        {who(c)} · {c.required ? "Needed" : "Optional"}
                        {!c.declared && " · Not asked for yet"}
                    </span>
                    <p>{c.why}</p>
                </li>
            ))}
        </ul>
    );
}

function Accounts({ plugin }: { plugin: Plugin }) {
    const accounts = plugin.accounts ?? [];
    const missing = plugin.missing ?? [];
    if (!accounts.length && !missing.length)
        return (
            <p className={s.faint}>
                {plugin.uses.length
                    ? "None of its own. It would work on what the plugins it uses already connected."
                    : "None. It would work on what you give it in the Lemma."}
            </p>
        );
    return (
        <ul className={s.accounts}>
            {accounts.map((a) => (
                <li key={anyOf(a)}>
                    <span className={s.service}>{anyOf(a)}</span>
                    <span className={s.accountMeta}>
                        {a.required
                            ? a.any.length > 1
                                ? "Needed: any one of these"
                                : "Needed"
                            : "Optional"}
                    </span>
                </li>
            ))}
            {missing.map((m) => (
                <li key={m}>
                    <span className={s.service}>{m}</span>
                    <span className={s.accountMeta} data-missing>
                        Wanted · Lemma can't connect to it yet
                    </span>
                </li>
            ))}
        </ul>
    );
}

function Runs({ parts }: { parts: BundleParts }) {
    const runs =
        parts.agents.length +
        parts.functions.length +
        parts.workflows.length +
        parts.schedules.length;
    return (
        <Section label="What runs it">
            <p className={s.counts}>
                <Count n={parts.agents.length} what="agent" />
                <Count n={parts.functions.length} what="function" />
                <Count n={parts.workflows.length} what="workflow" />
                <Count n={parts.schedules.length} what="schedule" />
                <Count n={parts.skills.length} what="skill" />
            </p>
            {runs === 0 && (
                <p className={s.faint}>
                    Nothing of its own: the plugin it needs does the work.
                </p>
            )}
            <Pieces title="Agents" items={parts.agents} />
            <Pieces
                title="Workflows"
                items={parts.workflows.map((w) => ({
                    name: w.name,
                    line: w.line,
                    note: w.starts,
                }))}
            />
            <Pieces
                title="Schedules"
                items={parts.schedules.map((sc) => ({
                    name: sc.name,
                    line: sc.when,
                    note: "Runs " + sc.runs,
                }))}
            />
            <Pieces title="Functions" items={parts.functions} />
        </Section>
    );
}

function Count({ n, what }: { n: number; what: string }) {
    return (
        <span>
            <b>{n}</b> {n === 1 ? what : what + "s"}
        </span>
    );
}

function Pieces({
    title,
    items,
}: {
    title: string;
    items: Array<{ name: string; line: string; note?: string }>;
}) {
    if (!items.length) return null;
    return (
        <details className={s.more}>
            <summary>
                {title} ({items.length})
            </summary>
            <ul className={s.pieces}>
                {items.map((i) => (
                    <li key={i.name}>
                        <code>{i.name}</code>
                        <span>{i.line}</span>
                        {i.note && <small>{i.note}</small>}
                    </li>
                ))}
            </ul>
        </details>
    );
}

function count(n: number, what: string) {
    return `${n} ${n === 1 ? what : what + "s"}`;
}
