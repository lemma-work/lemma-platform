import type { Metadata } from 'next';
import Link from 'next/link';

import { ArrowRight } from '@/components/ui/icons';

import { SiteFooter, SiteHeader } from '@/components/landing/site-chrome';
import { listContent } from '@/lib/content/loader';
import { socialCardPath } from '@/lib/share/social-card';

const TITLE = 'What we are building, and why.';
const DESCRIPTION = 'Build logs, pods, and notes on running agent-built software with a team.';

const image = socialCardPath({
    variant: 'build',
    title: TITLE,
    detail: DESCRIPTION,
    label: 'lemma.work/blog',
});

export const metadata: Metadata = {
    title: 'Blog',
    description: DESCRIPTION,
    alternates: { canonical: '/blog', types: { 'application/rss+xml': '/feed.xml' } },
    openGraph: {
        title: TITLE,
        description: DESCRIPTION,
        type: 'website',
        images: [{ url: image, width: 1200, height: 630, alt: TITLE }],
    },
    twitter: { card: 'summary_large_image', title: TITLE, description: DESCRIPTION, images: [image] },
};

function formatDate(iso: string): string {
    return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
    });
}

/**
 * A topic's colour, from the badge pairs the rest of the product already uses.
 *
 * Keyed off the tag, not the post's position, so a piece keeps its colour as
 * newer writing appears in front of it — and two posts on the same subject read
 * as the same subject at a glance.
 */
function topicTone(tag: string | undefined): string {
    switch (tag) {
        case 'product':
            return 'collaboration';
        case 'engineering':
        case 'agents':
            return 'intelligence';
        case 'pods':
        case 'templates':
            return 'success';
        case 'company':
            return 'delight';
        default:
            return 'collaboration';
    }
}

export default function BlogIndexPage() {
    const posts = listContent('blog');
    const [lead, ...rest] = posts;

    return (
        <div className="lp-react content-page">
            <SiteHeader hashPrefix="/" showThemeToggle />
            <div className="content-grid content-grid-single">
                <div className="content-column">
                    <header className="content-masthead">
                        <p className="content-eyebrow">Blog</p>
                        <h1 className="content-title">{TITLE}</h1>
                        <p className="content-dek">{DESCRIPTION}</p>
                    </header>

                    {posts.length === 0 ? (
                        <p className="content-index-empty">Nothing published yet.</p>
                    ) : (
                        <>
                            {/*
                              The newest post gets the lead slot — a full-width
                              tile in its topic's colour, the way the home page
                              leads with saturated tiles rather than a list. A
                              blog whose front page is an undifferentiated list
                              tells a reader nothing about where to start.
                            */}
                            <Link
                                className="content-lead"
                                data-tone={topicTone(lead.frontmatter.tags[0])}
                                href={`/blog/${lead.slug}`}
                            >
                                <p className="content-lead-meta">
                                    <time dateTime={lead.frontmatter.published}>
                                        {formatDate(lead.frontmatter.published)}
                                    </time>
                                    {lead.frontmatter.tags[0] ? (
                                        <span> · {lead.frontmatter.tags[0]}</span>
                                    ) : null}
                                </p>
                                <p className="content-lead-title">{lead.frontmatter.title}</p>
                                <p className="content-lead-dek">{lead.frontmatter.description}</p>
                                <span className="content-lead-cta">
                                    Read
                                    <ArrowRight className="content-lead-arrow" aria-hidden />
                                </span>
                            </Link>

                            {rest.length > 0 ? (
                                <ul className="content-cards">
                                    {rest.map((post) => (
                                        <li key={post.slug}>
                                            <Link
                                                className="content-card-post"
                                                data-tone={topicTone(post.frontmatter.tags[0])}
                                                href={`/blog/${post.slug}`}
                                            >
                                                <p className="content-card-post-meta">
                                                    <time dateTime={post.frontmatter.published}>
                                                        {formatDate(post.frontmatter.published)}
                                                    </time>
                                                    {post.frontmatter.tags[0] ? (
                                                        <span> · {post.frontmatter.tags[0]}</span>
                                                    ) : null}
                                                </p>
                                                <p className="content-card-post-title">
                                                    {post.frontmatter.title}
                                                    {post.frontmatter.draft ? (
                                                        <span className="content-index-draft">
                                                            Draft
                                                        </span>
                                                    ) : null}
                                                </p>
                                                <p className="content-card-post-dek">
                                                    {post.frontmatter.description}
                                                </p>
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            ) : null}
                        </>
                    )}
                </div>
            </div>
            <SiteFooter hashPrefix="/" />
        </div>
    );
}
