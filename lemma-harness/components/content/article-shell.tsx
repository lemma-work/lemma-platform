import Image from 'next/image';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { TocRail } from '@/components/content/toc-rail';
import { SiteFooter, SiteHeader } from '@/components/landing/site-chrome';
import { ArrowRight } from '@/components/ui/icons';
import type { ContentHeading } from '@/lib/content/headings';
import type { ContentDoc, ContentFrontmatter } from '@/lib/content/types';
import { getPublicTemplateBySlug, templateRunHref } from '@/lib/templates/catalog';

/**
 * The frame every dated article is read in.
 *
 * Three columns, not two. An article alone between two empty margins reads as
 * an isolated page and gives someone who finishes it nowhere to go; the left
 * rail carries the rest of the writing, so the page is a place in a publication
 * rather than a dead end.
 */

function formatDate(iso: string): string {
    return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
    });
}

/** The archive pane — what else there is to read, and what it is about. */
function ArchiveRail({ posts, activeSlug }: { posts: ContentDoc[]; activeSlug?: string }) {
    const tags = [...new Set(posts.flatMap((post) => post.frontmatter.tags))].sort();

    return (
        <aside className="content-archive" aria-label="More writing">
            <Link className="content-archive-all" href="/blog">
                All posts
            </Link>

            {posts.length > 0 ? (
                <div className="content-archive-group">
                    <p className="content-archive-title">Recent</p>
                    <ul>
                        {posts.slice(0, 6).map((post) => (
                            <li key={post.slug}>
                                <Link
                                    data-active={post.slug === activeSlug ? '' : undefined}
                                    href={`/blog/${post.slug}`}
                                >
                                    {post.frontmatter.title}
                                </Link>
                            </li>
                        ))}
                    </ul>
                </div>
            ) : null}

            {tags.length > 0 ? (
                <div className="content-archive-group">
                    <p className="content-archive-title">Topics</p>
                    <ul className="content-archive-tags">
                        {tags.map((tag) => (
                            <li key={tag}>{tag}</li>
                        ))}
                    </ul>
                </div>
            ) : null}
        </aside>
    );
}

/**
 * The end-of-post unit.
 *
 * Every article lands somewhere. When a piece names a pod in its frontmatter,
 * that somewhere is the import page for it — which is what makes writing part
 * of the share → import → remix loop rather than an essay that ends in a full
 * stop. Without a pod it degrades to the download, never to nothing.
 */
function ContentOutro({ pod }: { pod?: string }) {
    const template = pod ? getPublicTemplateBySlug(pod) : null;

    if (template) {
        return (
            <aside className="content-outro">
                <p className="content-outro-eyebrow">{template.kicker}</p>
                <p className="content-outro-title">{template.name}</p>
                <p className="content-outro-body">{template.description}</p>
                <Link className="content-outro-cta" href={templateRunHref(template)}>
                    Run this pod
                    <ArrowRight className="content-outro-arrow" aria-hidden />
                </Link>
            </aside>
        );
    }

    return (
        <aside className="content-outro">
            <p className="content-outro-title">Build the software your team needs.</p>
            <p className="content-outro-body">
                Lemma runs the apps, agents, workflows and data your coding agent writes.
            </p>
            <Link className="content-outro-cta" href="/download">
                Download Lemma
                <ArrowRight className="content-outro-arrow" aria-hidden />
            </Link>
        </aside>
    );
}

export function ArticleShell({
    frontmatter,
    headings,
    slug,
    siblings = [],
    children,
}: {
    frontmatter: ContentFrontmatter;
    headings: ContentHeading[];
    slug: string;
    siblings?: ContentDoc[];
    children: ReactNode;
}) {
    const { title, description, published, updated, author, pod, tags, cover } = frontmatter;

    return (
        <div className="lp-react content-page">
            <SiteHeader hashPrefix="/" showThemeToggle />
            <div className="content-grid">
                <ArchiveRail activeSlug={slug} posts={siblings} />

                <article className="content-column">
                    <header className="content-masthead">
                        <p className="content-kicker">
                            <time dateTime={published}>{formatDate(published)}</time>
                            {tags[0] ? <span className="content-kicker-tag">{tags[0]}</span> : null}
                        </p>
                        <h1 className="content-title">{title}</h1>
                        <p className="content-dek">{description}</p>
                        {author ? <p className="content-byline">{author}</p> : null}
                        {updated ? (
                            <p className="content-updated">Updated {formatDate(updated)}</p>
                        ) : null}
                    </header>

                    {/*
                      Only when the author supplied one. Defaulting to the
                      generated social card put a large pale panel at the top of
                      every article that restated the headline the reader had
                      just read — the card's job is the link unfurl, where it is
                      the only thing carrying the title. On the page it is
                      redundant, and it pushed the first paragraph below the
                      fold. `unoptimized` because a cover may be a `/api/…`
                      route the build is itself producing.
                    */}
                    {cover ? (
                        <div className="content-hero">
                            <Image alt="" height={630} priority src={cover} unoptimized width={1200} />
                        </div>
                    ) : null}

                    <div className="content-article">{children}</div>
                    <ContentOutro pod={pod} />
                </article>

                <TocRail headings={headings} />
            </div>
            <SiteFooter hashPrefix="/" />
        </div>
    );
}
