import type { Metadata } from 'next';

import { SiteFooter, SiteHeader } from '@/components/landing/site-chrome';
import { JsonLd } from '@/components/seo/json-ld';
import { compileContent } from '@/lib/content/compile';
import { listContent } from '@/lib/content/loader';
import { breadcrumbSchema } from '@/lib/seo/structured-data';
import { socialCardPath } from '@/lib/share/social-card';

const TITLE = 'What shipped, and when.';
const DESCRIPTION = 'Every Lemma release, newest first.';

const image = socialCardPath({
    variant: 'build',
    title: TITLE,
    detail: DESCRIPTION,
    label: 'lemma.work/changelog',
});

export const metadata: Metadata = {
    title: 'Changelog',
    description: DESCRIPTION,
    alternates: { canonical: '/changelog', types: { 'application/rss+xml': '/feed.xml' } },
    openGraph: {
        title: TITLE,
        description: DESCRIPTION,
        type: 'website',
        images: [{ url: image, width: 1200, height: 630, alt: TITLE }],
    },
    twitter: { card: 'summary_large_image', title: TITLE, description: DESCRIPTION, images: [image] },
};

/**
 * The dot's colour, from the release's first tag.
 *
 * Keyed off the tag rather than the entry's position so a release keeps the
 * same marker forever — a colour that shifts as newer releases are added in
 * front of it would make the timeline unreadable as a history.
 */
function releaseTone(tag: string | undefined): string {
    switch (tag) {
        case 'fix':
        case 'fixes':
            return 'success';
        case 'security':
        case 'breaking':
            return 'attention';
        case 'docs':
        case 'content':
            return 'intelligence';
        case 'improvement':
            return 'delight';
        default:
            return 'release';
    }
}

function formatDate(iso: string): string {
    return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        timeZone: 'UTC',
    });
}

/**
 * One page, every release, newest first.
 *
 * A changelog is read by scrolling, not by navigating — someone catching up
 * wants three releases in one pass, and paginating that costs them a page load
 * per version. Each entry still gets a stable `#id` so a single release can be
 * linked to directly.
 */
export default async function ChangelogPage() {
    const entries = await Promise.all(
        listContent('changelog').map(async (doc) => ({
            doc,
            compiled: await compileContent(doc.body),
        })),
    );

    return (
        <>
            <JsonLd
                schema={breadcrumbSchema([
                    { name: 'Lemma', path: '/' },
                    { name: 'Changelog' },
                ])}
            />
            <div className="lp-react content-page">
                <SiteHeader hashPrefix="/" showThemeToggle />
                <div className="content-grid content-grid-single content-grid-narrow">
                    <div className="content-column">
                        <header className="content-masthead">
                            <p className="content-eyebrow">Changelog</p>
                            <h1 className="content-title">{TITLE}</h1>
                            <p className="content-dek">{DESCRIPTION}</p>
                        </header>

                        {entries.length === 0 ? (
                            <p className="content-index-empty">No releases yet.</p>
                        ) : (
                            <ol className="content-timeline">
                                {entries.map(({ doc, compiled }) => {
                                    const tag = doc.frontmatter.tags[0];
                                    return (
                                        <li
                                            className="content-timeline-item"
                                            data-tone={releaseTone(tag)}
                                            id={doc.slug}
                                            key={doc.slug}
                                        >
                                            <p className="content-timeline-meta">
                                                <time dateTime={doc.frontmatter.published}>
                                                    {formatDate(doc.frontmatter.published)}
                                                </time>
                                                {tag ? <span> · {tag}</span> : null}
                                            </p>
                                            <div className="content-timeline-card">
                                                <h2 className="content-timeline-version">
                                                    <a href={`#${doc.slug}`}>
                                                        {doc.frontmatter.title}
                                                    </a>
                                                </h2>
                                                <div className="content-article">
                                                    {compiled.content}
                                                </div>
                                            </div>
                                        </li>
                                    );
                                })}
                            </ol>
                        )}
                    </div>
                </div>
                <SiteFooter hashPrefix="/" />
            </div>
        </>
    );
}
