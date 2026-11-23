/**
 * The shape of everything published from `content/`.
 *
 * Docs are a separate system today (`lib/data/docs.ts`, a typed block union).
 * This model is deliberately for *dated* content — writing that is published
 * once and then ages — because that is what docs blocks cannot express: an
 * author, a publication date, and a piece of prose whose components were not
 * anticipated when the union was written.
 */

/** Collections map one-to-one onto directories under `content/`. */
export const CONTENT_COLLECTIONS = ["blog", "changelog"] as const;

export type ContentCollection = (typeof CONTENT_COLLECTIONS)[number];

export interface ContentFrontmatter {
    title: string;
    /** Also the meta description and the social card's detail line. */
    description: string;
    /** ISO `YYYY-MM-DD`. Drives ordering, RSS, and `datePublished`. */
    published: string;
    /** ISO `YYYY-MM-DD`. Only set when a piece was meaningfully revised. */
    updated?: string;
    author?: string;
    tags: string[];
    /**
     * Hero image. Left unset, the article falls back to its generated social
     * card — the same 1200×630 the link unfurl uses. That is deliberate: every
     * post gets a real hero without commissioning art, and the image at the top
     * of the page is the image people saw before they clicked.
     */
    cover?: string;
    /**
     * Slug of the template this piece sends people to, if any. This is the
     * field that makes a post part of the share → import → remix loop instead
     * of an essay that ends in nothing.
     */
    pod?: string;
    /** Drafts render in development and are withheld everywhere else. */
    draft: boolean;
    /**
     * Changelog only. Set when the entry's section for API, SDK and CLI users
     * says a route, a field or a value is gone, so code written against the
     * last release stops working. What's new marks the entry "Breaking".
     */
    breaking?: boolean;
    /**
     * Changelog only. The release shipped a Lemma Desktop build. The newest
     * entry that says so offers the download, because `/download` serves the
     * latest build and nothing older.
     */
    desktop?: boolean;
    /**
     * Changelog only. The heading of the section written for API, SDK and CLI
     * users, when it is not the usual "What changes for API, SDK and CLI
     * users". What's new shows that section in a panel of its own.
     */
    developer?: string;
}

export interface ContentDoc {
    collection: ContentCollection;
    slug: string;
    /** Repo-relative, so a validation failure names the file to open. */
    source: string;
    frontmatter: ContentFrontmatter;
    /** Raw MDX, still uncompiled. */
    body: string;
}
