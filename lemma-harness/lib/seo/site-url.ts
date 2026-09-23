/**
 * The public origin this deployment is reachable at.
 *
 * `sitemap.ts`, `robots.ts` and every structured-data document have to agree on
 * one absolute origin — a sitemap that declares `https://lemma.work/docs/x`
 * while a JSON-LD `@id` says something else describes two different pages to a
 * crawler. This was copied into each of those files; it lives here now so the
 * fallback can only be spelled once.
 *
 * Note this is deliberately *not* `runtimeConfig`. Sitemaps and robots are
 * generated on the server at build time, where only `process.env` exists.
 */
export function publicSiteUrl(): string {
    const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim();
    return configured?.startsWith('http') ? configured.replace(/\/+$/, '') : 'https://lemma.work';
}

/** Absolute URL for a site-relative path, for the `url`/`@id` of a schema. */
export function absoluteUrl(path: string): string {
    const base = publicSiteUrl();
    if (!path || path === '/') return base;
    return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}
