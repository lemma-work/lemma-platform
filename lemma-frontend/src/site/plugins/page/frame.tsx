import Link from "next/link";
import type { ReactNode } from "react";
import { Lively } from "@/app/(marketing)/lively";
import m from "@/app/(marketing)/landing.module.css";
import { MarketingNav } from "@/app/(marketing)/nav";
import { SiteFooter } from "@/site/chrome";
import { BUILT, PLANNED } from "../catalog";
import s from "./page.module.css";
import t from "./theme.module.css";

/* What every plugin page sits in, whichever layout it has: the website's own
   frame (the nav, footer, warm ground and type of the home and product
   pages), the breadcrumb, and the plugins either side in the build order.
   The listing at `/templates` sits in the same frame. */

/** The home and product pages' frame: their pinned nav, their footer, and
 *  `Lively` (things rising in, margin notes drawing their arrows). Its tokens
 *  also have a dark rendition here (`theme.module.css`), since a plugin's
 *  pictures come in light and dark. */
export function MarketingFrame({ children }: { children: ReactNode }) {
    return (
        <div className={`${m.page} ${t.themed}`}>
            <a className={m.skip} href="#main">
                Skip to content
            </a>
            <MarketingNav />
            <main id="main">{children}</main>
            <SiteFooter />
            <Lively />
        </div>
    );
}

/** The plugins before and after this one in the build order, built or not. */
export function Neighbours({ position }: { position: number }) {
    const all = [...BUILT, ...PLANNED];
    const before = all.find((p) => p.position === position - 1);
    const after = all.find((p) => p.position === position + 1);
    return (
        <nav className={s.neighbours} aria-label="Plugins in build order">
            {before ? (
                <Link href={`/templates/${before.slug}`} rel="prev">
                    <small>Before it</small>
                    <span>← {before.name}</span>
                </Link>
            ) : (
                <span />
            )}
            <Link className={s.all} href="/templates">
                All plugins
            </Link>
            {after ? (
                <Link href={`/templates/${after.slug}`} rel="next" className={s.after}>
                    <small>After it</small>
                    <span>{after.name} →</span>
                </Link>
            ) : (
                <span />
            )}
        </nav>
    );
}

/** The older plugin page, until a plugin's landing page is written: its
 *  sheet of rows inside the website's frame. */
export function Frame({ children }: { children: ReactNode }) {
    return (
        <MarketingFrame>
            <div className={`${s.root} ${s.main}`}>{children}</div>
        </MarketingFrame>
    );
}

export function Crumbs({ name }: { name: string }) {
    return (
        <nav className={s.crumbs} aria-label="Breadcrumb">
            <Link href="/templates">Plugins</Link>
            <span aria-hidden="true">/</span>
            <span aria-current="page">{name}</span>
        </nav>
    );
}
