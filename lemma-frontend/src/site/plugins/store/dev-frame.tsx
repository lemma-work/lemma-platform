import "@/styles/docs.css";
import "@/styles/guide-tokens.css";
import "@/styles/docs-brand.css";

import type { ReactNode } from "react";

import { SiteFooter, SiteHeader } from "@/site/chrome";
import { guideFace } from "@/site/guide/guide-face";
import { productFace } from "@/ui/product-face";

import s from "./store.module.css";

/** What the plugin pages sit in: the docs' header and footer, the guide's
 *  faces and colours (`site--dev`), light and dark. `after` sits between the
 *  main column and the footer, for a full-width band. */
export function DevFrame({ children, after }: { children: ReactNode; after?: ReactNode }) {
    return (
        <div className={`site site--reading site--dev ${productFace.variable} ${guideFace.variable} ${s.page}`}>
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <SiteHeader />
            <main id="content" className={s.main}>
                {children}
            </main>
            {after}
            <SiteFooter />
        </div>
    );
}
