"use client";

import { useId } from "react";
import Link from "next/link";
import type { DocsDoor, DocsNavGroup } from "./docs-content";

const DOORS: [DocsDoor, string][] = [
    ["use", "Use Lemma"],
    ["build", "Build on Lemma"],
];

/** The list of every docs page: the guide first, then each half of the docs
 *  under its own heading, group by group. Nothing is hidden behind a switch,
 *  so any page is one look away. */
export function DocsNav({
    nav,
    current,
    onNavigate,
}: {
    /** Every group, each marked with the half that lists it. */
    nav: DocsNavGroup[];
    /** The current page's slug, "" on the docs home. */
    current: string;
    onNavigate?: () => void;
}) {
    const id = useId();
    return (
        <nav className="docs-list" aria-label="Docs">
            <Link className="docs-list__guide" href="/guide" onClick={onNavigate}>
                <span>The guide</span>
                <span className="docs-list__guide-d">Why Lemma works the way it does</span>
            </Link>
            {DOORS.map(([door, label]) => (
                <section key={door} className="docs-list__door" aria-labelledby={`${id}-${door}`}>
                    <p className="docs-list__door-h" id={`${id}-${door}`}>
                        {label}
                    </p>
                    {nav
                        .filter((group) => group.door === door)
                        .map((group) => (
                            <div key={group.title} className="docs-list__group">
                                {group.title !== label && <p className="docs-list__h">{group.title}</p>}
                                <ul>
                                    {group.pages.map((page) => (
                                        <li key={page.slug}>
                                            <Link
                                                href={"/docs/" + page.slug}
                                                aria-current={page.slug === current ? "page" : undefined}
                                                onClick={onNavigate}
                                            >
                                                {page.title}
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                </section>
            ))}
        </nav>
    );
}
