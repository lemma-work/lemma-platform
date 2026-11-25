import "@/styles/docs.css";
import "@/styles/guide-tokens.css";
import "@/styles/docs-brand.css";
import type { ReactNode } from "react";
import { SiteHeader } from "@/site/chrome";
import {
    buildDocsSearchIndex,
    docsInOrder,
    docsNav,
} from "@/site/docs-content";
import { DocsSearchButton, DocsShell } from "@/site/docs-shell";
import { guideFace } from "@/site/guide/guide-face";
import { productFace } from "@/ui/product-face";

/* Every /docs page shares this: the header with its search, the list of
   pages, and the search index, built here once from the pages themselves.
   The docs wear the developer brand, the guide's look: `site--dev` with
   the guide's faces and colours (`docs-brand.css`, `guide-tokens.css`). */
export default function DocsLayout({ children }: { children: ReactNode }) {
    return (
        <div className={`site site--reading site--dev ${productFace.variable} ${guideFace.variable}`}>
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <DocsShell
                header={<SiteHeader tools={<DocsSearchButton />} />}
                nav={docsNav()}
                index={buildDocsSearchIndex(docsInOrder())}
            >
                {children}
            </DocsShell>
        </div>
    );
}
