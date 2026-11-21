import "@/styles/docs.css";
import type { ReactElement } from "react";
import { Code, DownloadSimple, Rss } from "@phosphor-icons/react/dist/ssr";
import { listContent } from "@/site/content/loader";
import { compileContent } from "@/site/content/compile";
import { SiteFooter, SiteHeader } from "@/site/chrome";
import { HostedOnly } from "@/site/hosted-only";
import { pageMetadata } from "@/site/metadata";
import {
    changelogCounts,
    changelogEntry,
    desktopEntry,
    type ChangelogEntry,
} from "@/site/changelog-notes";
import {
    ChangelogEntryFrame,
    ChangelogNotes,
    ChangelogToggle,
    ChangelogView,
} from "@/site/changelog-view";
import { productFace } from "@/ui/product-face";

export const metadata = pageMetadata(
    "What's new",
    "Every Lemma release and update, newest first.",
    "/changelog",
);

/* What's new: every release and update, newest first, as a dated record to
   scan. Each entry shows what it holds; the newest is open, the rest open
   in place. */
export default async function Page() {
    const entries = listContent("changelog").map(changelogEntry);
    const compiled = await Promise.all(
        entries.map(async (entry) => ({
            notes: entry.notes
                ? (await compileContent(entry.notes)).content
                : null,
            developer: entry.developer
                ? (await compileContent(entry.developer)).content
                : null,
        })),
    );
    const download = desktopEntry(entries);
    return (
        <div className={`site site--reading ${productFace.variable}`}>
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <SiteHeader />
            <main id="content" className="log">
                <header className="log__head">
                    <div className="log__words">
                        <h1 className="docs-h1">What&rsquo;s new</h1>
                        <p className="docs-lede">
                            Every release and update, newest first.
                        </p>
                    </div>
                    <a className="b b--m b--secondary" href="/feed.xml">
                        <Rss aria-hidden="true" />
                        Subscribe with RSS
                    </a>
                </header>
                <ChangelogView
                    counts={changelogCounts(entries)}
                    kinds={Object.fromEntries(
                        entries.map((e) => [e.slug, e.kind]),
                    )}
                    newest={entries[0]?.slug ?? null}
                >
                    {entries.map((entry, i) => (
                        <Entry
                            key={entry.slug}
                            entry={entry}
                            notes={compiled[i].notes}
                            developer={compiled[i].developer}
                            download={entry.slug === download}
                        />
                    ))}
                </ChangelogView>
            </main>
            <SiteFooter />
        </div>
    );
}

function Entry({
    entry,
    notes,
    developer,
    download,
}: {
    entry: ChangelogEntry;
    notes: ReactElement | null;
    developer: ReactElement | null;
    download: boolean;
}) {
    const inside = entry.developer !== null || entry.sections.length > 0;
    return (
        <ChangelogEntryFrame slug={entry.slug} kind={entry.kind}>
            <div className="log-entry__when">
                {entry.kind === "release" ? (
                    <span className="log-vtag" data-kind="release">
                        {entry.version}
                    </span>
                ) : (
                    <span className="log-vtag" data-kind="update">
                        <i aria-hidden="true" />
                        Update
                    </span>
                )}
                <time dateTime={entry.published}>{entry.date}</time>
            </div>
            <div className="log-entry__body">
                <h2>
                    <a href={"#" + encodeURIComponent(entry.slug)}>{entry.heading}</a>
                </h2>
                <p className="log-sum">{entry.description}</p>
                {inside && (
                    <ul className="log-inside" aria-label="In these notes">
                        {entry.developer !== null && (
                            <li data-dev>For API, SDK and CLI users</li>
                        )}
                        {entry.breaking && <li data-breaking>Breaking</li>}
                        {entry.sections.map((title) => (
                            <li key={title}>{title}</li>
                        ))}
                    </ul>
                )}
                <ChangelogNotes slug={entry.slug}>
                    {developer && (
                        <section
                            className="log-dev"
                            id={entry.developerId ?? undefined}
                            aria-labelledby={entry.slug + "-dev"}
                        >
                            <h3 id={entry.slug + "-dev"}>
                                <Code aria-hidden="true" />
                                For API, SDK and CLI users
                                {entry.breaking && (
                                    <span className="log-breaking">
                                        Breaking
                                    </span>
                                )}
                            </h3>
                            <div className="log-prose">{developer}</div>
                        </section>
                    )}
                    {notes && <div className="log-prose">{notes}</div>}
                    {download && (
                        <HostedOnly>
                            <div className="log-download">
                                <DownloadSimple aria-hidden="true" />
                                <span>Lemma Desktop {entry.version}</span>
                                <a
                                    className="b b--s b--secondary"
                                    href="/download"
                                >
                                    Download
                                </a>
                            </div>
                        </HostedOnly>
                    )}
                </ChangelogNotes>
                <ChangelogToggle slug={entry.slug} />
            </div>
        </ChangelogEntryFrame>
    );
}
