import { docsPageMap } from "@/site/data/docs";
import { aboutPage, contactPage } from "@/site/data/company-pages";
import { privacyPolicy, termsOfService } from "@/site/data/legal";
import { docsIndexMarkdown } from "@/site/markdown/docs-index";
import { homepageMarkdown } from "@/site/markdown/homepage";
import {
    legalDocumentToMarkdown,
    docsPageToMarkdown,
} from "@/site/markdown/render";

/**
 * The routes that negotiate `text/markdown` (see middleware.ts), and how each
 * one renders. Every entry here is content this site already has in
 * structured form — nothing is scraped from the HTML it also renders.
 */
export function markdownForPath(pathname: string): string | null {
    if (pathname === "/") return homepageMarkdown();
    if (pathname === "/docs") return docsIndexMarkdown();
    if (pathname === "/privacy") return legalDocumentToMarkdown(privacyPolicy);
    if (pathname === "/tos") return legalDocumentToMarkdown(termsOfService);
    if (pathname === "/about") return legalDocumentToMarkdown(aboutPage);
    if (pathname === "/contact") return legalDocumentToMarkdown(contactPage);

    const docsSlug = pathname.match(/^\/docs\/(.+)$/)?.[1];
    if (docsSlug) {
        const page = docsPageMap.get(docsSlug);
        if (page) return docsPageToMarkdown(page);
    }

    return null;
}
