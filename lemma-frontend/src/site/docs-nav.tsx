"use client";
import { useState } from "react";
import Link from "next/link";
export function DocsNav({
    pages,
    current,
}: {
    pages: { slug: string; title: string; group: string }[];
    current?: string;
}) {
    const [search, setSearch] = useState("");
    const [mobileOpen, setMobileOpen] = useState(false);
    const filtered = pages.filter((p) =>
        (p.title + " " + p.group).toLowerCase().includes(search.toLowerCase()),
    );
    const groups = [...new Set(filtered.map((p) => p.group))];
    return (
        <aside className="site-docs-nav">
            <div className={"docs-guide-menu" + (mobileOpen ? " is-open" : "")}>
                <button className="docs-menu-toggle" aria-expanded={mobileOpen} aria-controls="docs-guides" onClick={() => setMobileOpen(!mobileOpen)}>Browse documentation <span>{mobileOpen ? "−" : "+"}</span></button>
                <div id="docs-guides" className="docs-guide-content">
                    <label className="docs-search">
                        Find a guide
                        <input
                            type="search"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Search guides…"
                        />
                    </label>
                    <nav aria-label="Documentation" onClick={() => setMobileOpen(false)}>
                        <Link
                            href="/docs"
                            aria-current={!current ? "page" : undefined}
                        >
                            Documentation home
                        </Link>
                        {groups.map((group) => (
                            <section key={group}>
                                <h2>{group}</h2>
                                {filtered
                                    .filter((p) => p.group === group)
                                    .map((p) => (
                                        <Link
                                            key={p.slug}
                                            href={"/docs/" + p.slug}
                                            aria-current={
                                                p.slug === current
                                                    ? "page"
                                                    : undefined
                                            }
                                        >
                                            {p.title}
                                        </Link>
                                    ))}
                            </section>
                        ))}
                        {!filtered.length && (
                            <p role="status">No guides match “{search}”.</p>
                        )}
                    </nav>
                </div>
            </div>
        </aside>
    );
}
