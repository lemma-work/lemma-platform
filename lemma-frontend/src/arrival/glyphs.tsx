/** Each app's own mark on the way in: a line drawing of what it is, on a
 *  quiet tile. Characters belong to Lemmas; an app is a thing you use. */

const PATHS: Record<string, string> = {
    mail: "M3.5 6.5A2.5 2.5 0 0 1 6 4h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 20H6a2.5 2.5 0 0 1-2.5-2.5zM4.5 7l7.5 6 7.5-6",
    meetings: "M3.5 7.5A2.5 2.5 0 0 1 6 5h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 21H6a2.5 2.5 0 0 1-2.5-2.5zM3.5 10h17M8 3v4M16 3v4",
    today: "M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4M12 8.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7z",
    "people-map": "M9 8m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.9.8 3.1 2.6 3.5 5.2",
    tasks: "M9 6h11M9 12h11M9 18h11M4 6l1.2 1.2L7 5.4M4 12l1.2 1.2L7 11.4M4 18l1.2 1.2L7 17.4",
    "deal-tracker": "M4 4h4v16H4zM10 4h4v11h-4zM16 4h4v7h-4z",
    "booking-page": "M3.5 7.5A2.5 2.5 0 0 1 6 5h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 21H6a2.5 2.5 0 0 1-2.5-2.5zM3.5 10h17M12 13v5M9.5 15.5h5",
    "lead-research": "M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13zM15.5 15.5L20 20",
    "brand-kit": "M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.1-.9-1.4-.9-2.4 0-1 .8-1.6 1.8-1.6h2.1a3.7 3.7 0 0 0 3.7-3.7C20.5 7 16.7 3.5 12 3.5zM7.5 11.5h.01M10 7.5h.01M15 7.5h.01",
    "deck-maker": "M4 5h16v11H4zM12 16v4M8 20h8",
};

const FALLBACK = "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z";

/** The line drawing alone, for a tile drawn by its caller. */
export function GlyphIcon({ slug, size = 20 }: { slug: string | null; size?: number }) {
    return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={slug ? PATHS[slug] ?? FALLBACK : "M12 7v10M7 12h10"} />
        </svg>
    );
}

export function AppGlyph({ slug, size = 40 }: { slug: string; size?: number }) {
    const icon = Math.round(size * 0.5);
    return (
        <span className="ar__glyph" style={{ width: size, height: size }} aria-hidden="true">
            <svg width={icon} height={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d={PATHS[slug] ?? FALLBACK} />
            </svg>
        </span>
    );
}
