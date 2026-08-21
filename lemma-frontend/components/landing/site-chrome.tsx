'use client';

import Link from 'next/link';
import { useState } from 'react';

import { Logo } from '@/components/brand/logo';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { GithubLogo } from '@/components/ui/icons';
import { copyrightNotice } from '@/lib/company';
import { githubUrl } from './landing-data';

/**
 * The site header and footer, lifted out of the landing page so every public
 * page wears the same chrome.
 *
 * These were inline in `landing-page.tsx`, which is why the blog, changelog and
 * docs each grew their own near-miss version. There is one now.
 *
 * Two things to know before reusing this:
 *
 * 1. The styles are scoped under `.lp-react` in `landing-page.css`. A consumer
 *    must render inside that class or the chrome arrives unstyled — `SiteChrome`
 *    below exists so nobody has to remember.
 * 2. The nav carries hash links to landing sections. Off the landing page those
 *    resolve against the wrong document, so `hashPrefix` rewrites them to `/#…`.
 */

type NavLink = { label: string; href: string; external?: boolean };

const NAV_LINKS: NavLink[] = [
    { label: 'How it works', href: '#loop' },
    { label: 'Templates', href: '/templates' },
    { label: 'Docs', href: '/docs', external: true },
];

function navHref(link: NavLink, hashPrefix: string): string {
    return link.href.startsWith('#') ? `${hashPrefix}${link.href}` : link.href;
}

function NavItem({
    link,
    hashPrefix,
    onNavigate,
}: {
    link: NavLink;
    hashPrefix: string;
    onNavigate?: () => void;
}) {
    const href = navHref(link, hashPrefix);

    if (link.external) {
        return (
            <a href={href} onClick={onNavigate} rel="noreferrer" target="_blank">
                {link.label}
            </a>
        );
    }
    if (href.startsWith('#')) {
        return (
            <a href={href} onClick={onNavigate}>
                {link.label}
            </a>
        );
    }
    return (
        <Link href={href} onClick={onNavigate}>
            {link.label}
        </Link>
    );
}

export function SiteHeader({
    hashPrefix = '',
    showThemeToggle = false,
}: {
    hashPrefix?: string;
    /**
     * Off by default. The landing is a light-only visual world with a fixed
     * palette, so a switch there would offer an appearance it cannot render;
     * content pages follow the reader's theme and do want it.
     */
    showThemeToggle?: boolean;
}) {
    const [menuOpen, setMenuOpen] = useState(false);

    return (
        <>
            <header className="lp-header" aria-label="Site header">
                <Link className="lp-brand" href="/" aria-label="Lemma home">
                    <Logo className="lp-brand-logo" size="sm" variant="mark-wordmark" />
                </Link>
                <nav className="lp-nav" aria-label="Primary navigation">
                    {NAV_LINKS.map((link) => (
                        <NavItem hashPrefix={hashPrefix} key={link.label} link={link} />
                    ))}
                    <a className="lp-gh-link" href={githubUrl} rel="noreferrer" target="_blank">
                        <GithubLogo aria-hidden className="lp-gh-icon" />
                        GitHub
                    </a>
                </nav>
                <div className="lp-header-actions">
                    {showThemeToggle ? <ThemeToggle variant="icon" /> : null}
                    <Link className="lp-button primary" href="/auth">
                        Start building
                    </Link>
                    {/* Below the nav breakpoint this was the only control in the header,
                        leaving Templates, Docs and GitHub unreachable from a phone. */}
                    <button
                        aria-controls="lp-mobile-menu"
                        aria-expanded={menuOpen}
                        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                        className="lp-menu-toggle"
                        onClick={() => setMenuOpen((open) => !open)}
                        type="button"
                    >
                        <span className={menuOpen ? 'is-open' : ''} />
                    </button>
                </div>
            </header>

            <div
                className={`lp-mobile-menu${menuOpen ? ' is-open' : ''}`}
                hidden={!menuOpen}
                id="lp-mobile-menu"
            >
                {NAV_LINKS.map((link) => (
                    <NavItem
                        hashPrefix={hashPrefix}
                        key={link.label}
                        link={link}
                        onNavigate={() => setMenuOpen(false)}
                    />
                ))}
                <a href={githubUrl} rel="noreferrer" target="_blank">
                    GitHub
                </a>
            </div>
        </>
    );
}

export function SiteFooter({ hashPrefix = '' }: { hashPrefix?: string }) {
    return (
        <footer className="lp-site-footer">
            <div className="lp-site-footer-inner">
                <div className="lp-site-footer-brand">
                    <Logo className="lp-brand-logo" size="sm" variant="mark-wordmark" />
                    <p>The runtime for agent-built software.</p>
                </div>

                <nav aria-label="Product">
                    <p className="lp-site-footer-label">Product</p>
                    <a href={`${hashPrefix}#loop`}>How it works</a>
                    <Link href="/templates">Templates</Link>
                    <Link href="/auth">Start building</Link>
                </nav>

                <nav aria-label="Developers">
                    <p className="lp-site-footer-label">Developers</p>
                    <Link href="/docs">Docs</Link>
                    {/* Plain anchors, not Link: these are file/route responses,
                        not app-router pages, so client-side navigation to them
                        has nothing to render. */}
                    <a href="/openapi.json">OpenAPI spec</a>
                    <a href="/llms.txt">llms.txt</a>
                    <Link href="/blog">Blog</Link>
                    <Link href="/changelog">Changelog</Link>
                </nav>

                <nav aria-label="Company">
                    <p className="lp-site-footer-label">Company</p>
                    <Link href="/about">About</Link>
                    <Link href="/contact">Contact</Link>
                    <Link href="/privacy">Privacy</Link>
                    <Link href="/tos">Terms</Link>
                </nav>

                <nav aria-label="Licence">
                    <p className="lp-site-footer-label">Licence</p>
                    <a href={githubUrl} rel="noreferrer" target="_blank">
                        GitHub
                    </a>
                    <a href={`${githubUrl}/blob/main/LICENSE`} rel="noreferrer" target="_blank">
                        AGPLv3 core
                    </a>
                    <a
                        href={`${githubUrl}/blob/main/LICENSE-APACHE`}
                        rel="noreferrer"
                        target="_blank"
                    >
                        Apache-2.0 SDKs
                    </a>
                </nav>
            </div>

            <p className="lp-site-footer-base">
                <span>Open source. Run it anywhere.</span>
                <span>{copyrightNotice()}</span>
            </p>
        </footer>
    );
}
