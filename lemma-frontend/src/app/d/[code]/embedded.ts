/** What a shared page shows from elsewhere in the pod, pointed somewhere a
 *  stranger's browser can reach.
 *
 *  A page writes its pictures as pod paths — `Plan-files/chart.png`,
 *  `/pages/Plan-files/chart.png` — which mean nothing to a reader with no
 *  session. Each is sent instead to `/d/<code>/a?ref=<the reference as
 *  written>`, and the API decides from the page as it is now whether that
 *  reference travels with it (`embedded_references.py`). The reference rather
 *  than a resolved path, so this page never needs to know where in the pod the
 *  document lives, and the one place that reads references is the one that
 *  decides.
 *
 *  Only what loads into the page is rewritten — `src`, `srcset`, `poster`, a
 *  stylesheet or icon `<link>`, CSS `url()` and `@import`. A link to another
 *  document is not part of this page and is left alone. Pure, so the rules are
 *  tested rather than trusted. */

const SCHEME = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/** Whether a reference is the pod's, rather than a URL of its own. The same
 *  test the API applies, so the two agree on what is worth asking for. */
export function isPodReference(reference: string): boolean {
    const value = reference.trim().replace(/^<|>$/g, "").trim();
    return value !== "" && !value.startsWith("#") && !value.startsWith("//") && !SCHEME.test(value);
}

export function embeddedUrl(code: string, reference: string): string {
    return "/d/" + encodeURIComponent(code) + "/a?ref=" + encodeURIComponent(reference.trim());
}

/** A picture in a markdown page: through the link if it is the pod's, as
 *  itself if it is on the web, and not at all otherwise. */
export function imageSource(code: string, src: string): string | null {
    if (isPodReference(src)) return embeddedUrl(code, src);
    return /^https:\/\//i.test(src.trim()) ? src : null;
}

function rewriteUrl(code: string, value: string): string {
    return isPodReference(value) ? embeddedUrl(code, value) : value;
}

function rewriteSrcset(code: string, value: string): string {
    return value
        .split(",")
        .map((candidate) => {
            const trimmed = candidate.trim();
            if (!trimmed) return candidate;
            const [url, ...descriptor] = trimmed.split(/\s+/);
            return [rewriteUrl(code, url), ...descriptor].join(" ");
        })
        .join(", ");
}

function rewriteCss(code: string, css: string): string {
    return css
        .replace(/url\(\s*(["']?)([^"')]*)\1\s*\)/gi, (whole, quote: string, url: string) =>
            isPodReference(url) ? "url(" + quote + embeddedUrl(code, url) + quote + ")" : whole,
        )
        .replace(/@import\s+(["'])([^"']*)\1/gi, (whole, quote: string, url: string) =>
            isPodReference(url) ? "@import " + quote + embeddedUrl(code, url) + quote : whole,
        );
}

const LOADING_TAG = /<(img|source|video|audio|track|script|link)\b[^>]*>/gi;
const LOADING_ATTRIBUTE = /(\s(src|srcset|poster|href)\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/gi;
const STYLE_ATTRIBUTE = /(\sstyle\s*=\s*)(?:"([^"]*)"|'([^']*)')/gi;
const STYLE_ELEMENT = /(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi;
const LINK_LOADS = /\brel\s*=\s*["']?[^"'>]*\b(stylesheet|icon|apple-touch-icon)\b/i;

function rewriteTag(code: string, tag: string, name: string): string {
    const isLink = name.toLowerCase() === "link";
    if (isLink && !LINK_LOADS.test(tag)) return tag;
    return tag.replace(LOADING_ATTRIBUTE, (whole, lead: string, attribute: string, double?: string, single?: string, bare?: string) => {
        const key = attribute.toLowerCase();
        if (key === "href" && !isLink) return whole;
        if (key !== "href" && isLink) return whole;
        const value = double ?? single ?? bare ?? "";
        const next = key === "srcset" ? rewriteSrcset(code, value) : rewriteUrl(code, value);
        return lead + '"' + next.replace(/"/g, "&quot;") + '"';
    });
}

/** A shared HTML page with everything it loads from the pod sent through the link. */
export function rewriteHtml(html: string, code: string): string {
    return html
        .replace(STYLE_ELEMENT, (_, open: string, css: string, close: string) => open + rewriteCss(code, css) + close)
        .replace(LOADING_TAG, (tag, name: string) => rewriteTag(code, tag, name))
        .replace(STYLE_ATTRIBUTE, (_, lead: string, double?: string, single?: string) => {
            const quote = double !== undefined ? '"' : "'";
            return lead + quote + rewriteCss(code, double ?? single ?? "") + quote;
        });
}
