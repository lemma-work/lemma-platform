import Link from "next/link";
import { githubUrl } from "./links";
import styles from "./footer.module.css";
import type { ReactNode } from "react";
import { LemmaLogo } from "@/ui/icons";
import { copyrightNotice } from "./company";
import { HostedOnly } from "./hosted-only";
export function SiteFooter() {
    const groups = [
        {
            title: "Explore",
            links: [
                ["Examples", "/templates"],
                ["Documentation", "/docs"],
                ["Download", "/download"],
                ["Changelog", "/changelog"],
            ],
        },
        {
            title: "Build with us",
            links: [
                ["GitHub ↗", githubUrl],
                ["Report an issue ↗", githubUrl + "/issues"],
                ["Contribute ↗", githubUrl + "/blob/main/CONTRIBUTING.md"],
                ["Open-source license ↗", githubUrl + "/blob/main/LICENSE"],
            ],
        },
        {
            title: "Company",
            links: [
                ["About", "/about"],
                ["Blog", "/blog"],
                ["Contact", "/contact"],
                ["Privacy", "/privacy"],
                ["Terms", "/tos"],
            ],
        },
    ];
    return (
        <footer id="footer" className={styles.footer}>
            <div className={styles.directory}>
                <div className={styles.brand}>
                    <Link href="/" aria-label="Lemma home">
                        <LemmaLogo />
                    </Link>
                    <p>
                        AI teammates for ongoing work.
                    </p>
                    <a className={styles.oss} href={githubUrl}>
                        Open source on GitHub ↗
                    </a>
                </div>
                {groups.map((group) => (
                    <nav key={group.title} aria-label={group.title}>
                        <h3>{group.title}</h3>
                        {group.links.map(([label, href]) => href === "/download"
                            ? <HostedOnly key={href}><a href={href}>{label}</a></HostedOnly>
                            : <a key={href} href={href}>{label}</a>)}
                    </nav>
                ))}
            </div>
            <div className={styles.bottom}>
                <small>{copyrightNotice()}</small>
                <nav aria-label="Machine-readable resources">
                    <a href="/llms.txt">llms.txt</a>
                    <a href="/openapi.json">OpenAPI</a>
                    <a href="/feed.xml">RSS</a>
                </nav>
                <span>Core: AGPLv3 · SDKs: Apache 2.0</span>
            </div>
        </footer>
    );
}
export function SitePage({
    title,
    description,
    children,
    wide = false,
}: {
    title: string;
    description?: string;
    children: ReactNode;
    wide?: boolean;
}) {
    return (
        <div className="site">
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <SiteHeader />
            <main
                id="content"
                className={wide ? "site-main site-main--wide" : "site-main"}
            >
                <header className="site-title">
                    <p>LEMMA</p>
                    <h1>{title}</h1>
                    {description && <p>{description}</p>}
                </header>
                {children}
            </main>
            <SiteFooter />
        </div>
    );
}
export function JsonLd({ schema }: { schema: Record<string, unknown> }) {
    return (
        <script
            type="application/ld+json"
            dangerouslySetInnerHTML={{
                __html: JSON.stringify(schema).replace(/</g, "\u003c"),
            }}
        />
    );
}

export function SiteHeader() {
    return (
        <header className="site-header">
            <Link href="/" aria-label="Lemma home">
                <LemmaLogo />
            </Link>
            <nav aria-label="Main navigation">
                <Link href="/templates">Templates</Link>
                <Link href="/docs">Docs</Link>
                <HostedOnly><Link href="/download">Download</Link></HostedOnly>
                <a href={githubUrl}>GitHub ↗</a>
                <Link href="/t">Get started ↗</Link>
            </nav>
        </header>
    );
}
