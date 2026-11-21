import "@/styles/docs.css";
import type { ReactNode } from "react";
import { SiteHeader } from "@/site/chrome";
import {
    USE_LEMMA_TOPICS,
    buildDocsSearchIndex,
    docsInOrder,
    docsNav,
} from "@/site/docs-content";
import { DocsSearchButton, DocsShell } from "@/site/docs-shell";
import { productFace } from "@/ui/product-face";

/* Every /docs page shares this: the header with its search, the guide list,
   and the search index, built here once from the pages themselves. */
export default function DocsLayout({ children }: { children: ReactNode }) {
    return (
        <div className={`site site--reading ${productFace.variable}`}>
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <DocsShell
                header={<SiteHeader tools={<DocsSearchButton />} />}
                nav={docsNav()}
                topics={USE_LEMMA_TOPICS}
                index={buildDocsSearchIndex(docsInOrder())}
            >
                {children}
            </DocsShell>
        </div>
    );
}
