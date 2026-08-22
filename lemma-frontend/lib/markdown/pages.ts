import { docsPageMap } from '@/lib/data/docs';
import { aboutPage, contactPage } from '@/lib/data/company-pages';
import { privacyPolicy, termsOfService } from '@/lib/data/legal';
import { docsIndexMarkdown } from '@/lib/markdown/docs-index';
import { homepageMarkdown } from '@/lib/markdown/homepage';
import { legalDocumentToMarkdown, docsPageToMarkdown } from '@/lib/markdown/render';

/**
 * The routes that negotiate `text/markdown` (see middleware.ts), and how each
 * one renders. Every entry here is content this site already has in
 * structured form — nothing is scraped from the HTML it also renders.
 */
export function markdownForPath(pathname: string): string | null {
    if (pathname === '/') return homepageMarkdown();
    if (pathname === '/docs') return docsIndexMarkdown();
    if (pathname === '/privacy') return legalDocumentToMarkdown(privacyPolicy);
    if (pathname === '/tos') return legalDocumentToMarkdown(termsOfService);
    if (pathname === '/about') return legalDocumentToMarkdown(aboutPage);
    if (pathname === '/contact') return legalDocumentToMarkdown(contactPage);

    const docsSlug = pathname.match(/^\/docs\/(.+)$/)?.[1];
    if (docsSlug) {
        const page = docsPageMap.get(docsSlug);
        if (page) return docsPageToMarkdown(page);
    }

    return null;
}
