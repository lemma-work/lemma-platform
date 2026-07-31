import type { MetadataRoute } from 'next';

function publicSiteUrl(): string {
    const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim();
    return configured?.startsWith('http') ? configured.replace(/\/+$/, '') : 'https://lemma.work';
}

export default function robots(): MetadataRoute.Robots {
    return {
        rules: {
            userAgent: '*',
            allow: ['/', '/templates/', '/docs/', '/import/github/'],
            disallow: [
                '/home',
                '/pod/',
                '/organizations/',
                '/profile',
                '/connectors',
                '/conversations',
            ],
        },
        sitemap: `${publicSiteUrl()}/sitemap.xml`,
    };
}
