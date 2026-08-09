/**
 * JSON-LD documents for the public surfaces.
 *
 * Open Graph tells a social crawler how to draw a card. Structured data tells a
 * search engine what a page *is* — that `/docs/quickstart` is technical
 * documentation published by a named organisation and sitting third in a
 * breadcrumb, not an untyped bag of text. It is what earns breadcrumb trails
 * and sitelinks in a result, and this codebase emitted none of it.
 *
 * Every builder returns a plain object. Rendering is `<JsonLd>`'s job, so a
 * schema can be unit-tested without a DOM.
 */

import { COMPANY_LEGAL_NAME } from '@/lib/company';
import { absoluteUrl, publicSiteUrl } from '@/lib/seo/site-url';

export type JsonLdSchema = Record<string, unknown>;

const ORGANIZATION_ID = `${publicSiteUrl()}/#organization`;
const WEBSITE_ID = `${publicSiteUrl()}/#website`;

/**
 * The publisher every article points back at.
 *
 * `@id` is the load-bearing part: an article referencing the organisation by id
 * rather than restating its name lets a crawler merge them into one entity
 * instead of inferring a new publisher per page.
 */
export function organizationSchema(): JsonLdSchema {
    return {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        '@id': ORGANIZATION_ID,
        name: 'Lemma',
        legalName: COMPANY_LEGAL_NAME,
        url: publicSiteUrl(),
        logo: absoluteUrl('/icon-192.png'),
        sameAs: ['https://github.com/lemma-work'],
    };
}

export function webSiteSchema(): JsonLdSchema {
    return {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        '@id': WEBSITE_ID,
        name: 'Lemma',
        url: publicSiteUrl(),
        publisher: { '@id': ORGANIZATION_ID },
    };
}

/**
 * Lemma itself, for the pages that are selling it rather than explaining it.
 *
 * `offers` at price 0 is not a marketing claim — it is how an open-source
 * application says "no paywall between you and the download", and omitting it
 * makes the listing look incomplete rather than free.
 */
export function softwareApplicationSchema(): JsonLdSchema {
    return {
        '@context': 'https://schema.org',
        '@type': 'SoftwareApplication',
        name: 'Lemma',
        applicationCategory: 'DeveloperApplication',
        operatingSystem: 'macOS, Windows, Linux',
        url: publicSiteUrl(),
        downloadUrl: absoluteUrl('/download'),
        publisher: { '@id': ORGANIZATION_ID },
        offers: {
            '@type': 'Offer',
            price: '0',
            priceCurrency: 'USD',
        },
    };
}

export interface TechArticleInput {
    title: string;
    description?: string;
    /** Site-relative path, e.g. `/docs/quickstart`. */
    path: string;
    /** ISO 8601. Omitted rather than faked when a page carries no real date. */
    published?: string;
    modified?: string;
    section?: string;
}

/**
 * A documentation page.
 *
 * `TechArticle` rather than `Article` because the distinction is real to a
 * search engine: technical documentation is ranked for task-shaped queries
 * ("how do I …") in a way generic articles are not.
 *
 * Dates are optional and never invented. A wrong `dateModified` is worse than
 * none — it teaches the crawler that this site's freshness signals are noise.
 */
export function techArticleSchema(input: TechArticleInput): JsonLdSchema {
    const url = absoluteUrl(input.path);
    return {
        '@context': 'https://schema.org',
        '@type': 'TechArticle',
        '@id': `${url}#article`,
        headline: input.title,
        ...(input.description ? { description: input.description } : {}),
        url,
        mainEntityOfPage: { '@type': 'WebPage', '@id': url },
        ...(input.section ? { articleSection: input.section } : {}),
        ...(input.published ? { datePublished: input.published } : {}),
        ...(input.modified ? { dateModified: input.modified } : {}),
        publisher: { '@id': ORGANIZATION_ID },
        isPartOf: { '@id': WEBSITE_ID },
    };
}

export interface BreadcrumbItem {
    name: string;
    /** Site-relative path. The final crumb may omit it — it is the current page. */
    path?: string;
}

export function breadcrumbSchema(items: BreadcrumbItem[]): JsonLdSchema {
    return {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        itemListElement: items.map((item, index) => ({
            '@type': 'ListItem',
            position: index + 1,
            name: item.name,
            ...(item.path ? { item: absoluteUrl(item.path) } : {}),
        })),
    };
}
