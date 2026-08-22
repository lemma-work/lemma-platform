import type { MetadataRoute } from 'next';

import { collectionLastModified, listContent } from '@/lib/content/loader';
import { docsPages } from '@/lib/data/docs';
import { publicSiteUrl } from '@/lib/seo/site-url';
import { PUBLIC_TEMPLATES, templateRunHref } from '@/lib/templates/catalog';

export default function sitemap(): MetadataRoute.Sitemap {
    const base = publicSiteUrl();
    const fixed: MetadataRoute.Sitemap = [
        { url: base, changeFrequency: 'weekly', priority: 1 },
        { url: `${base}/templates`, changeFrequency: 'weekly', priority: 0.9 },
        { url: `${base}/download`, changeFrequency: 'weekly', priority: 0.9 },
        { url: `${base}/docs`, changeFrequency: 'weekly', priority: 0.8 },
        { url: `${base}/about`, changeFrequency: 'monthly', priority: 0.5 },
        { url: `${base}/contact`, changeFrequency: 'monthly', priority: 0.5 },
    ];
    const docs: MetadataRoute.Sitemap = docsPages
        .filter((page) => page.slug !== 'overview')
        .map((page) => ({
            url: `${base}/docs/${page.slug}`,
            changeFrequency: 'monthly',
            priority: 0.65,
        }));
    // Not `/templates/<slug>` — that route is a noindex redirect stub. The page
    // a template actually resolves to is its import URL, which renders the
    // repository's README server-side and is the thing worth ranking for the
    // problem the pod solves.
    const templates: MetadataRoute.Sitemap = PUBLIC_TEMPLATES.map((template) => ({
        url: `${base}${templateRunHref(template)}`,
        changeFrequency: 'weekly',
        priority: 0.8,
    }));
    /*
     * These carry a real `lastModified`, which nothing else here can. Docs and
     * templates have no recorded modification date, and inventing one would
     * teach a crawler that this site's freshness signals are noise — worse than
     * declaring none at all. Frontmatter is the first place a true date exists.
     */
    const blogPosts = listContent('blog');
    const blogLastModified = collectionLastModified('blog');
    const changelogLastModified = collectionLastModified('changelog');

    const content: MetadataRoute.Sitemap = [
        ...(blogLastModified
            ? [
                  {
                      url: `${base}/blog`,
                      lastModified: blogLastModified,
                      changeFrequency: 'weekly' as const,
                      priority: 0.8,
                  },
              ]
            : []),
        ...(changelogLastModified
            ? [
                  {
                      url: `${base}/changelog`,
                      lastModified: changelogLastModified,
                      changeFrequency: 'weekly' as const,
                      priority: 0.7,
                  },
              ]
            : []),
        ...blogPosts.map((post) => ({
            url: `${base}/blog/${post.slug}`,
            lastModified: post.frontmatter.updated ?? post.frontmatter.published,
            changeFrequency: 'monthly' as const,
            priority: 0.75,
        })),
    ];

    return [...fixed, ...content, ...templates, ...docs];
}
