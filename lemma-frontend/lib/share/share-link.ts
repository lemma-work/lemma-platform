/**
 * Public share links.
 *
 * A workspace URL is useless to a crawler: `/pod/*` is signed-in-only and
 * carries no Open Graph tags, so a shared agent unfurls as nothing. `/s/…`
 * fixes that with a page a crawler can actually read.
 *
 * It invents no information. The kind, the pod and the resource slug are all
 * already in the URL the sharer chose to send, and the description on the card
 * is fixed per-kind copy — nothing is read from the backend, so a share link
 * cannot leak anything the link itself did not already contain.
 */

import type { SocialCardVariant } from '@/lib/share/social-card';

/** Short, URL-friendly names for the resource types that can be shared. */
export type ShareKind =
    | 'agent'
    | 'app'
    | 'workflow'
    | 'function'
    | 'table'
    | 'document'
    | 'folder'
    | 'schedule'
    | 'pod';

/** The share-dialog vocabulary, which uses the backend's resource type names. */
export type ShareResourceType =
    | 'agent'
    | 'function'
    | 'workflow'
    | 'schedule'
    | 'datastore_table'
    | 'document'
    | 'folder'
    | 'app';

const KIND_BY_RESOURCE_TYPE: Record<ShareResourceType, ShareKind> = {
    agent: 'agent',
    function: 'function',
    workflow: 'workflow',
    schedule: 'schedule',
    datastore_table: 'table',
    document: 'document',
    folder: 'folder',
    app: 'app',
};

interface ShareKindCopy {
    /** Sentence-case noun, e.g. "Agent". */
    noun: string;
    /** Reads after the name: "Support Triage — an agent on Lemma". */
    article: string;
    variant: SocialCardVariant;
}

const SHARE_KIND_COPY: Record<ShareKind, ShareKindCopy> = {
    agent: { noun: 'Agent', article: 'an agent', variant: 'agent' },
    app: { noun: 'App', article: 'an app', variant: 'app' },
    workflow: { noun: 'Workflow', article: 'a workflow', variant: 'workflow' },
    function: { noun: 'Function', article: 'a function', variant: 'function' },
    table: { noun: 'Table', article: 'a table', variant: 'table' },
    document: { noun: 'Document', article: 'a document', variant: 'document' },
    folder: { noun: 'Folder', article: 'a folder', variant: 'document' },
    schedule: { noun: 'Schedule', article: 'a schedule', variant: 'schedule' },
    pod: { noun: 'Pod', article: 'a pod', variant: 'run' },
};

const SHARE_KINDS = Object.keys(SHARE_KIND_COPY) as ShareKind[];

/** The query key carrying the display name, so the card reads how people wrote it. */
export const SHARE_NAME_PARAM = 'n';

export function isShareKind(value: string | null | undefined): value is ShareKind {
    return SHARE_KINDS.includes(value as ShareKind);
}

export function shareKindForResourceType(resourceType: ShareResourceType): ShareKind {
    return KIND_BY_RESOURCE_TYPE[resourceType];
}

export function getShareKindCopy(kind: ShareKind): ShareKindCopy {
    return SHARE_KIND_COPY[kind];
}

/**
 * Wraps a canonical workspace URL in a shareable one.
 *
 * The destination path rides along verbatim — including its query, which is
 * where apps, tables and documents keep their identity — so any resource round
 * trips without a per-kind reconstruction table that could drift from the app's
 * real routes.
 */
export function buildShareLink(input: {
    kind: ShareKind;
    /** Absolute canonical URL, e.g. https://lemma.work/pod/p1/agents/triage. */
    canonicalUrl: string;
    name?: string | null;
}): string | null {
    let url: URL;
    try {
        url = new URL(input.canonicalUrl);
    } catch {
        return null;
    }

    if (!url.pathname.startsWith('/pod/')) return null;

    const params = new URLSearchParams(url.search);
    const name = input.name?.replace(/\s+/g, ' ').trim();
    if (name) params.set(SHARE_NAME_PARAM, name.slice(0, 120));
    const query = params.toString();

    return `${url.origin}/s/${input.kind}${url.pathname}${query ? `?${query}` : ''}`;
}

/**
 * Rebuilds the workspace path a share link points at.
 *
 * Returns null unless the result is a workspace-relative `/pod/…` path. The
 * segments come from the router already split on `/`, and the leading-slash
 * check rejects `//evil.com`, so this can never redirect off-origin.
 */
export function resolveShareDestination(
    segments: string[] | undefined,
    query?: Record<string, string | string[] | undefined>,
): string | null {
    const path = `/${(segments ?? []).filter(Boolean).join('/')}`;
    if (!/^\/pod\/[^/]+/.test(path)) return null;
    if (path.startsWith('//')) return null;

    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query ?? {})) {
        if (key === SHARE_NAME_PARAM || value === undefined) continue;
        for (const entry of Array.isArray(value) ? value : [value]) {
            params.append(key, entry);
        }
    }

    const search = params.toString();
    return search ? `${path}?${search}` : path;
}

/**
 * The name to print on the card.
 *
 * Prefers the display name the sharer's link carried; otherwise makes the URL
 * slug presentable. Never consults the backend.
 */
export function resolveShareName(input: {
    name?: string | null;
    segments?: string[];
    query?: Record<string, string | string[] | undefined>;
}): string | null {
    const explicit = input.name?.replace(/\s+/g, ' ').trim();
    if (explicit) return explicit.slice(0, 120);

    // Apps and tables keep their identity in the query rather than the path.
    for (const key of ['page', 'table', 'file', 'folder']) {
        const value = input.query?.[key];
        const candidate = Array.isArray(value) ? value[0] : value;
        if (candidate) return prettifySlug(candidate);
    }

    const last = (input.segments ?? []).filter(Boolean).at(-1);
    return last ? prettifySlug(last) : null;
}

/** `support-triage` → `Support Triage`. */
export function prettifySlug(value: string): string {
    const cleaned = value
        .split('/')
        .filter(Boolean)
        .at(-1)
        ?.replace(/\.[a-z0-9]{1,8}$/i, '')
        .replace(/[-_]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    if (!cleaned) return '';
    return cleaned
        .split(' ')
        .map((word) => (word ? word[0].toUpperCase() + word.slice(1) : word))
        .join(' ')
        .slice(0, 120);
}
