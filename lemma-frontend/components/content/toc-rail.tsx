'use client';

import { useEffect, useState } from 'react';

import type { ContentHeading } from '@/lib/content/headings';

/**
 * The on-this-page rail, with the section you are reading marked.
 *
 * The only client component in the reading stack, and it earns that by doing
 * something server HTML genuinely cannot: reflect scroll position. The headings
 * themselves arrive as props from the compiled document, so the list still
 * renders and links correctly with JavaScript switched off — the active state
 * is the enhancement, not the content.
 */
export function TocRail({ headings }: { headings: ContentHeading[] }) {
    const [activeId, setActiveId] = useState<string>(headings[0]?.id ?? '');

    useEffect(() => {
        if (headings.length === 0) return;

        const elements = headings
            .map((heading) => document.getElementById(heading.id))
            .filter((element): element is HTMLElement => element !== null);
        if (elements.length === 0) return;

        /*
         * The band is the top ~35% of the viewport. A heading is "current" once
         * it reaches that zone, which matches where a reader's eye actually is —
         * keying off the exact top edge makes the highlight flicker between two
         * sections on every small scroll.
         */
        const observer = new IntersectionObserver(
            (entries) => {
                const visible = entries
                    .filter((entry) => entry.isIntersecting)
                    .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
                if (visible[0]) setActiveId(visible[0].target.id);
            },
            { rootMargin: '-80px 0px -65% 0px', threshold: 0 },
        );

        elements.forEach((element) => observer.observe(element));
        return () => observer.disconnect();
    }, [headings]);

    if (headings.length < 2) return null;

    return (
        <nav className="content-rail" aria-labelledby="content-rail-title">
            <p className="content-rail-title" id="content-rail-title">
                On this page
            </p>
            <ul>
                {headings.map((heading) => (
                    <li key={heading.id} className={`content-rail-h${heading.depth}`}>
                        <a
                            aria-current={heading.id === activeId ? 'location' : undefined}
                            data-active={heading.id === activeId ? '' : undefined}
                            href={`#${heading.id}`}
                        >
                            {heading.text}
                        </a>
                    </li>
                ))}
            </ul>
        </nav>
    );
}
