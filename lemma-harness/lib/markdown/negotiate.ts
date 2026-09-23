/**
 * Accept-header negotiation for the acceptmarkdown.com convention: a page
 * that normally serves `text/html` should serve `text/markdown` instead when
 * a client asks for it, on the same URL, rather than at a separate `.md`
 * route. https://acceptmarkdown.com
 *
 * `Vary: Accept` is the other half of the contract — it tells a cache that
 * the response for this URL depends on the Accept header, so an agent's
 * markdown request and a browser's HTML request never collide in the same
 * cache slot. Every response from a negotiated route must carry it,
 * regardless of which representation it ends up serving.
 */

type MediaRange = { type: string; subtype: string; q: number };

function parseAccept(accept: string): MediaRange[] {
    return accept
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => {
            const [mediaType, ...params] = part.split(';').map((p) => p.trim());
            const [type, subtype] = mediaType.split('/');
            const qParam = params.find((p) => p.startsWith('q='));
            const q = qParam ? parseFloat(qParam.slice(2)) : 1;
            return { type: type || '*', subtype: subtype || '*', q: Number.isFinite(q) ? q : 1 };
        });
}

function matches(range: MediaRange, type: string, subtype: string): boolean {
    return (range.type === '*' || range.type === type) && (range.subtype === '*' || range.subtype === subtype);
}

function isFullWildcard(range: MediaRange): boolean {
    return range.type === '*' && range.subtype === '*';
}

function preferenceFor(
    ranges: MediaRange[],
    type: string,
    subtype: string,
    { countFullWildcard = true }: { countFullWildcard?: boolean } = {}
): number {
    let best = -1;
    for (const range of ranges) {
        if (!countFullWildcard && isFullWildcard(range)) continue;
        if (matches(range, type, subtype) && range.q > best) {
            best = range.q;
        }
    }
    return best;
}

/**
 * True when the Accept header prefers `text/markdown` over `text/html` —
 * markdown is present with a q-value at least as high as html's (or html is
 * absent entirely). An empty or absent Accept header, or one that prefers
 * html, returns false: html stays the default representation so ordinary
 * browsers are never affected by this.
 *
 * A bare full wildcard (any type, any subtype) does not count as asking for
 * markdown, even though it technically matches — it is the "no real
 * preference" catch-all most clients append, not an explicit request for
 * this representation.
 */
export function prefersMarkdown(acceptHeader: string | null | undefined): boolean {
    if (!acceptHeader) return false;
    const ranges = parseAccept(acceptHeader);
    const markdownQ = preferenceFor(ranges, 'text', 'markdown', { countFullWildcard: false });
    if (markdownQ <= 0) return false;
    const htmlQ = preferenceFor(ranges, 'text', 'html');
    return markdownQ >= htmlQ;
}
