import type { MetadataRoute } from 'next';

import { publicSiteUrl } from '@/lib/seo/site-url';

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
