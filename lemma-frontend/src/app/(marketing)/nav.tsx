import Link from "next/link";
import { discordUrl, githubUrl } from "@/site/links";
import { LemmaLogo } from "@/ui/icons";
import { PRODUCT_PAGES } from "@/marketing/product-pages";
import s from "./landing.module.css";
import p from "./product.module.css";

/** The marketing pages' navigation: over the landing's hero, and pinned to
 *  the top of every product page. Product opens on hover or focus, one row
 *  per part of a teammate's space; on a phone the menu lists them too. */
export function MarketingNav({ over = false }: { over?: boolean }) {
    return (
        <header className={over ? s.nav : `${s.nav} ${p.navPinned}`}>
            <div className={`${s.wrap} ${s.navInner}`}>
                <Link href="/" className={s.wordmark} aria-label="Lemma home">
                    <LemmaLogo />
                </Link>
                <nav aria-label="Main navigation" className={s.navLinks}>
                    <div className={p.product}>
                        <button type="button" className={p.productButton} aria-haspopup="true">Product</button>
                        <div className={p.menu}>
                            {PRODUCT_PAGES.map(page => (
                                <Link key={page.slug} href={"/product/" + page.slug}>
                                    <b>{page.name}</b>
                                    <small>{page.navBlurb}</small>
                                </Link>
                            ))}
                        </div>
                    </div>
                    <Link href="/docs">Docs</Link>
                    <Link href="/templates">Templates</Link>
                    <a href={githubUrl} target="_blank" rel="noreferrer">GitHub</a>
                    <a href={discordUrl} target="_blank" rel="noreferrer">Discord</a>
                    <Link href="/t">Sign in</Link>
                    <Link href="/t" className={s.navStart}>Get started</Link>
                </nav>
                <details className={s.mobileMenu}>
                    <summary>Menu</summary>
                    <div>
                        {PRODUCT_PAGES.map(page => <Link key={page.slug} href={"/product/" + page.slug}>{page.name}</Link>)}
                        <Link href="/docs">Docs</Link>
                        <Link href="/templates">Templates</Link>
                        <a href={githubUrl} target="_blank" rel="noreferrer">GitHub ↗</a>
                        <a href={discordUrl} target="_blank" rel="noreferrer">Discord ↗</a>
                        <Link href="/t">Sign in</Link>
                    </div>
                </details>
            </div>
        </header>
    );
}
