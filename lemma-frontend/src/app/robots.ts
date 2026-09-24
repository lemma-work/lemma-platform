import type { MetadataRoute } from 'next';

import { publicSiteUrl } from '@/site/seo/site-url';

export default function robots(): MetadataRoute.Robots {
    return {
        rules: {
            userAgent: '*',
            allow: ['/', '/templates/', '/docs/', '/import/github/'],
            disallow: [
                '/t/', '/auth/', '/connect', '/demo/', '/characters/', '/d/', '/home',
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
