import GithubSlugger from "github-slugger";
import { notFound } from "next/navigation";
import Link from "next/link";
import { docsPages, docsPageMap, getAdjacentDocsPages } from "@/site/data/docs";
import { docsPageToMarkdown } from "@/site/markdown/render";
import { SiteHeader, JsonLd } from "@/site/chrome";
import { Prose } from "@/site/prose";
import { DocsNav } from "@/site/docs-nav";
import { ConceptMap } from "@/site/concept-map";
import { pageMetadata } from "@/site/metadata";
import { breadcrumbSchema } from "@/site/seo/structured-data";
type Props = { params: Promise<{ slug?: string[] }> };
export function generateStaticParams() {
    return [
        { slug: [] },
        ...docsPages.map((p) => ({ slug: p.slug.split("/") })),
    ];
}
export async function generateMetadata({ params }: Props) {
    const slug = (await params).slug?.join("/");
    const p = slug ? docsPageMap.get(slug) : null;
    return pageMetadata(
        p?.title ?? "Documentation",
        p?.description ?? "Learn the Lemma platform, SDK, and CLI.",
        slug ? "/docs/" + slug : "/docs",
    );
}
export default async function Page({ params }: Props) {
    const slug = (await params).slug?.join("/");
    const p = slug ? docsPageMap.get(slug) : null;
    if (slug && !p) notFound();
    const title = p?.title ?? "Documentation";
    const description =
        p?.description ??
        "Everything you need to build, connect, and work with your AI teammates.";
    const slugger = new GithubSlugger();
    const outline =
        p?.blocks
            .filter((b) => b.type !== "callout" && b.title)
            .map((b) => ({ title: b.title!, id: slugger.slug(b.title!) })) ??
        [];
    const adjacent = p ? getAdjacentDocsPages(p) : null;
    return (
        <div className="site docs-shell">
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <SiteHeader />
            <JsonLd
                schema={breadcrumbSchema([
                    { name: "Lemma", path: "/" },
                    { name: "Docs", path: "/docs" },
                    ...(p ? [{ name: p.title }] : []),
                ])}
            />
            <div className="docs-layout">
                <DocsNav
                    current={slug}
                    pages={docsPages.map(({ slug, title, group }) => ({
                        slug,
                        title,
                        group,
                    }))}
                />
                <main id="content" className="docs-article">
                    <header className="docs-heading">
                        <p className="docs-eyebrow">
                            {p?.group ?? "LEMMA DOCS"}
                        </p>
                        <h1>{title}</h1>
                        <p>{description}</p>
                    </header>
                    {p ? (
                        <>
                            {slug === "how-lemma-works" && <ConceptMap />}
                            <Prose>
                                {docsPageToMarkdown(p)
                                    .split("\n\n")
                                    .slice(2)
                                    .join("\n\n")}
                            </Prose>
                            <nav
                                className="docs-pagination"
                                aria-label="Adjacent guides"
                            >
                                {adjacent?.previous ? (
                                    <Link
                                        href={"/docs/" + adjacent.previous.slug}
                                    >
                                        <small>← Previous</small>
                                        {adjacent.previous.title}
                                    </Link>
                                ) : (
                                    <span />
                                )}
                                {adjacent?.next && (
                                    <Link href={"/docs/" + adjacent.next.slug}>
                                        <small>Next →</small>
                                        {adjacent.next.title}
                                    </Link>
                                )}
                            </nav>
                        </>
                    ) : (
                        <>
                            <Link
                                className="docs-start"
                                href="/docs/getting-started"
                            >
                                <span>START HERE</span>
                                <h2>Your first steps with Lemma ↗</h2>
                                <p>
                                    Set up your workspace and get your first
                                    teammate working.
                                </p>
                            </Link>
                            <h2 className="docs-explore">
                                Explore the documentation
                            </h2>
                            <div className="site-grid">
                                {docsPages
                                    .filter((p) =>
                                        [
                                            "overview",
                                            "sdk/installation",
                                            "cli/overview",
                                            "guides/first-agent",
                                            "how-lemma-works",
                                        ].includes(p.slug),
                                    )
                                    .map((p) => (
                                        <Link
                                            className="site-card"
                                            key={p.slug}
                                            href={"/docs/" + p.slug}
                                        >
                                            <small>{p.group}</small>
                                            <h2>{p.title} ↗</h2>
                                            <p>{p.description}</p>
                                        </Link>
                                    ))}
                            </div>
                        </>
                    )}
                    <footer className="docs-bottom">
                        <Link href="/contact">Need a hand? Contact us ↗</Link>
                        <Link href="/templates">Explore examples ↗</Link>
                    </footer>
                </main>
                {outline.length > 0 && (
                    <aside className="docs-outline">
                        <nav aria-label="On this page">
                            <h2>On this page</h2>
                            {outline.map((h) => (
                                <a key={h.id} href={"#" + h.id}>
                                    {h.title}
                                </a>
                            ))}
                        </nav>
                    </aside>
                )}
            </div>
        </div>
    );
}
