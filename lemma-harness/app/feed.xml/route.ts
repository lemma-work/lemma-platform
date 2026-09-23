import { listContent } from '@/lib/content/loader';
import { absoluteUrl, publicSiteUrl } from '@/lib/seo/site-url';
import type { ContentDoc } from '@/lib/content/types';

/**
 * One feed for everything dated.
 *
 * Blog and changelog share a feed rather than splitting into two nobody
 * subscribes to separately — someone following Lemma wants to know both that we
 * wrote something and that we shipped something.
 */

/**
 * `<` and `&` in a title would otherwise produce a feed that fails to parse in
 * every reader. Escaping is done by hand because the payload is five fields
 * wide and a templating dependency for it would be heavier than the problem.
 */
function escapeXml(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function pubDate(iso: string): string {
    return new Date(`${iso}T00:00:00Z`).toUTCString();
}

function itemPath(doc: ContentDoc): string {
    // The changelog is one page, so an entry's permalink is a fragment on it
    // rather than a route of its own.
    return doc.collection === 'changelog' ? `/changelog#${doc.slug}` : `/blog/${doc.slug}`;
}

function toItem(doc: ContentDoc): string {
    const url = absoluteUrl(itemPath(doc));
    return [
        '    <item>',
        `      <title>${escapeXml(doc.frontmatter.title)}</title>`,
        `      <link>${escapeXml(url)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(url)}</guid>`,
        `      <description>${escapeXml(doc.frontmatter.description)}</description>`,
        `      <pubDate>${pubDate(doc.frontmatter.published)}</pubDate>`,
        `      <category>${escapeXml(doc.collection)}</category>`,
        '    </item>',
    ].join('\n');
}

export const dynamic = 'force-static';

export function GET(): Response {
    const docs = [...listContent('blog'), ...listContent('changelog')].sort((a, b) =>
        a.frontmatter.published < b.frontmatter.published ? 1 : -1,
    );

    const body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        '  <channel>',
        '    <title>Lemma</title>',
        `    <link>${publicSiteUrl()}</link>`,
        '    <description>Build the AI software your team needs. Run it on Lemma.</description>',
        '    <language>en</language>',
        `    <atom:link href="${absoluteUrl('/feed.xml')}" rel="self" type="application/rss+xml" />`,
        ...docs.map(toItem),
        '  </channel>',
        '</rss>',
    ].join('\n');

    return new Response(body, {
        headers: { 'content-type': 'application/rss+xml; charset=utf-8' },
    });
}
