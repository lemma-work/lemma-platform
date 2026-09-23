import 'server-only';

import fs from 'node:fs';
import path from 'node:path';

import matter from 'gray-matter';
import { CORE_SCHEMA, load as loadYaml } from 'js-yaml';

import {
    CONTENT_COLLECTIONS,
    type ContentCollection,
    type ContentDoc,
    type ContentFrontmatter,
} from '@/lib/content/types';

/**
 * Reads `content/` off disk.
 *
 * `server-only` is the first import on purpose. This module touches `fs` at
 * module scope, so a client component importing it — even transitively, even by
 * accident through a barrel file — must fail the build loudly rather than
 * produce a cryptic bundler error about `node:fs`.
 *
 * Frontmatter is *validated*, never trusted. A misspelled key or a date typed
 * as `2026-13-01` would otherwise sail through YAML parsing and surface much
 * later as a post sorted into the wrong decade, an invalid `datePublished` in
 * JSON-LD, and a broken RSS entry. Every failure here throws with the file path
 * attached, at build time, where it costs one line to fix.
 */

const CONTENT_ROOT = path.join(process.cwd(), 'content');

/**
 * gray-matter still calls `yaml.safeLoad`, which js-yaml removed in v4 — and
 * this project pins js-yaml to 4.2.0 through `overrides`, so the two cannot
 * agree on their own. Handing gray-matter an explicit engine settles it here
 * rather than leaving the pipeline dependent on which version happens to hoist.
 *
 * `stringify` throws because nothing writes frontmatter back; a silent no-op
 * would turn a future write into lost data.
 *
 * `CORE_SCHEMA` rather than the default is the load-bearing choice. YAML's
 * default schema resolves an unquoted `2026-08-09` into a `Date`, so a
 * perfectly ordinary frontmatter date arrives as an object, serialises as
 * `2026-08-09T00:00:00.000Z`, and drifts by a timezone the moment anyone
 * formats it. CORE keeps timestamps as the strings they were written as while
 * still parsing `true`/`false` for `draft` — frontmatter means what it says.
 */
const YAML_ENGINE = {
    parse: (input: string) => loadYaml(input, { schema: CORE_SCHEMA }) as object,
    stringify: () => {
        throw new Error('Content frontmatter is read-only.');
    },
};

/** `YYYY-MM-DD`, and a date that actually exists on a calendar. */
function parseIsoDate(value: unknown, field: string, source: string): string {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        throw new Error(`${source}: "${field}" must be a YYYY-MM-DD string, got ${JSON.stringify(value)}`);
    }
    // `new Date('2026-02-31')` rolls over to March rather than failing, so the
    // round-trip is what proves the date is real.
    const parsed = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
        throw new Error(`${source}: "${field}" is not a real date: ${value}`);
    }
    return value;
}

function requireString(value: unknown, field: string, source: string): string {
    if (typeof value !== 'string' || value.trim() === '') {
        throw new Error(`${source}: "${field}" is required and must be a non-empty string`);
    }
    return value.trim();
}

function parseFrontmatter(data: Record<string, unknown>, source: string): ContentFrontmatter {
    const tags = data.tags ?? [];
    if (!Array.isArray(tags) || tags.some((tag) => typeof tag !== 'string')) {
        throw new Error(`${source}: "tags" must be a list of strings`);
    }
    if (data.draft !== undefined && typeof data.draft !== 'boolean') {
        throw new Error(`${source}: "draft" must be true or false`);
    }
    if (data.pod !== undefined && typeof data.pod !== 'string') {
        throw new Error(`${source}: "pod" must be a template slug`);
    }
    if (data.cover !== undefined && typeof data.cover !== 'string') {
        throw new Error(`${source}: "cover" must be an image path`);
    }

    const published = parseIsoDate(data.published, 'published', source);
    const updated = data.updated === undefined ? undefined : parseIsoDate(data.updated, 'updated', source);
    if (updated && updated < published) {
        throw new Error(`${source}: "updated" (${updated}) is before "published" (${published})`);
    }

    return {
        title: requireString(data.title, 'title', source),
        description: requireString(data.description, 'description', source),
        published,
        updated,
        author: data.author === undefined ? undefined : requireString(data.author, 'author', source),
        tags: tags as string[],
        pod: data.pod,
        cover: data.cover,
        draft: data.draft === true,
    };
}

/**
 * Drafts are visible while writing and invisible once deployed.
 *
 * Keyed off `NODE_ENV` rather than the deployment flag: a draft must never
 * reach a production build, including a self-hosted one.
 */
function includeDrafts(): boolean {
    return process.env.NODE_ENV !== 'production';
}

function collectionDir(collection: ContentCollection): string {
    return path.join(CONTENT_ROOT, collection);
}

function readDoc(collection: ContentCollection, fileName: string): ContentDoc {
    const source = path.join('content', collection, fileName);
    const raw = fs.readFileSync(path.join(collectionDir(collection), fileName), 'utf8');
    const { data, content } = matter(raw, { engines: { yaml: YAML_ENGINE } });
    return {
        collection,
        slug: fileName.replace(/\.mdx$/, ''),
        source,
        frontmatter: parseFrontmatter(data as Record<string, unknown>, source),
        body: content,
    };
}

/**
 * Every published doc in a collection, newest first.
 *
 * Ties break on slug so the order is total — two posts sharing a date must not
 * reorder between builds, or every build churns the RSS feed.
 */
export function listContent(collection: ContentCollection): ContentDoc[] {
    const dir = collectionDir(collection);
    if (!fs.existsSync(dir)) return [];

    return fs
        .readdirSync(dir)
        .filter((name) => name.endsWith('.mdx'))
        .map((name) => readDoc(collection, name))
        .filter((doc) => includeDrafts() || !doc.frontmatter.draft)
        .sort((a, b) => {
            if (a.frontmatter.published !== b.frontmatter.published) {
                return a.frontmatter.published < b.frontmatter.published ? 1 : -1;
            }
            return a.slug < b.slug ? -1 : 1;
        });
}

export function getContent(collection: ContentCollection, slug: string): ContentDoc | null {
    return listContent(collection).find((doc) => doc.slug === slug) ?? null;
}

/** Every doc across every collection — for the sitemap and cross-collection feeds. */
export function listAllContent(): ContentDoc[] {
    return CONTENT_COLLECTIONS.flatMap((collection) => listContent(collection));
}

/** The most recent date any doc in a collection carries, for sitemap freshness. */
export function collectionLastModified(collection: ContentCollection): string | null {
    const docs = listContent(collection);
    if (docs.length === 0) return null;
    return docs.reduce((latest, doc) => {
        const stamp = doc.frontmatter.updated ?? doc.frontmatter.published;
        return stamp > latest ? stamp : latest;
    }, '0000-00-00');
}
