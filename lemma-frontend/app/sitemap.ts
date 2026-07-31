import type { MetadataRoute } from 'next';

import { docsPages } from '@/lib/data/docs';

function publicSiteUrl(): string {
    const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim();
    return configured?.startsWith('http') ? configured.replace(/\/+$/, '') : 'https://lemma.work';
}

export default function sitemap(): MetadataRoute.Sitemap {
    const base = publicSiteUrl();
    const fixed: MetadataRoute.Sitemap = [
        { url: base, changeFrequency: 'weekly', priority: 1 },
        { url: `${base}/templates`, changeFrequency: 'weekly', priority: 0.9 },
        { url: `${base}/docs`, changeFrequency: 'weekly', priority: 0.8 },
    ];
    const docs: MetadataRoute.Sitemap = docsPages
        .filter((page) => page.slug !== 'overview')
        .map((page) => ({
            url: `${base}/docs/${page.slug}`,
            changeFrequency: 'monthly',
            priority: 0.65,
        }));
    return [...fixed, ...docs];
}
