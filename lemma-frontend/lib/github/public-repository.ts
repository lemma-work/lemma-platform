export interface PublicGitHubRepository {
    name: string;
    full_name: string;
    html_url: string;
    description: string | null;
    default_branch: string;
    stargazers_count: number;
    updated_at: string;
    owner: {
        login: string;
        avatar_url: string;
    };
    license?: {
        spdx_id?: string | null;
        name?: string | null;
    } | null;
}

export interface PublicGitHubReadme {
    markdown: string;
    branch: string;
    repository: PublicGitHubRepository | null;
}

export interface ReadmePresentation {
    title: string;
    intro: string;
    coverImage: string | null;
    body: string;
}

const PUBLIC_GITHUB_REQUEST_TIMEOUT_MS = 6000;

async function fetchWithTimeout(input: string, init?: RequestInit): Promise<Response> {
    const controller = new AbortController();
    const timeoutId = setTimeout(
        () => controller.abort(),
        PUBLIC_GITHUB_REQUEST_TIMEOUT_MS,
    );

    try {
        return await fetch(input, { ...init, signal: controller.signal });
    } finally {
        clearTimeout(timeoutId);
    }
}

// `&amp;` must be decoded last, not first. Decoding it first re-creates entities
// that the later passes then decode a second time, so a README containing the
// literal text `&amp;lt;script&amp;gt;` comes out as `<script>` — text the
// author escaped on purpose, silently turned back into markup.
function decodeHtmlEntities(value: string): string {
    return value
        .replaceAll('&lt;', '<')
        .replaceAll('&gt;', '>')
        .replaceAll('&quot;', '"')
        .replaceAll('&#39;', "'")
        .replaceAll('&nbsp;', ' ')
        .replaceAll('&amp;', '&');
}

// A single pass is not a strip. `<<p>p>` leaves `<p>` behind, because removing
// the inner match splices the outer one back together -- so anything built on
// one pass is claiming a guarantee it does not have. Repeat until the string
// stops changing; each pass only ever removes characters, so it terminates.
function stripUntilStable(value: string, pattern: RegExp, replacement: string): string {
    let current = value;
    for (;;) {
        const next = current.replace(pattern, replacement);
        if (next === current) return current;
        current = next;
    }
}

function stripHtml(value: string): string {
    return decodeHtmlEntities(stripUntilStable(value, /<[^>]+>/g, ' '))
        .replace(/\s+/g, ' ')
        .trim();
}

function humanizeRepositoryName(value: string): string {
    return value
        .replace(/[-_]+/g, ' ')
        .replace(/\b\w/g, (letter) => letter.toUpperCase())
        .trim();
}

// Matching a host as a substring of the whole URL is not matching a host:
// `https://example.com/?x=shields.io` contains it, and so does
// `https://shields.io.example.com/`. Parse once and ask the URL its own
// questions. A README image may be a relative path, which has no host at all --
// the placeholder base exists only so parsing succeeds, and its host never
// matches any of the tests below.
const RELATIVE_IMAGE_BASE = 'https://readme.invalid';

function isDecorativeReadmeImage(value: string): boolean {
    let url: URL;
    try {
        url = new URL(value.trim(), RELATIVE_IMAGE_BASE);
    } catch {
        return false;
    }
    const host = url.hostname.toLowerCase();
    const path = url.pathname.toLowerCase();
    const isBadgeHost = host === 'shields.io' || host.endsWith('.shields.io');
    const isOwnActions =
        host === 'github.com' && path.startsWith('/lemma-work/lemma-platform/actions/');
    return (
        isBadgeHost ||
        isOwnActions ||
        path.includes('/badge') ||
        path.includes('install-remix-on-lemma')
    );
}

function readmeImages(markdown: string): string[] {
    const images: string[] = [];
    for (const match of markdown.matchAll(/<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)) {
        if (match[1]) images.push(match[1].trim());
    }
    for (const match of markdown.matchAll(/!\[[^\]]*]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/gi)) {
        if (match[1]) images.push(match[1].trim());
    }
    return images;
}

function readmeIntro(markdown: string): string {
    for (const match of markdown.matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/gi)) {
        if (/<img\b/i.test(match[1])) continue;
        const candidate = stripHtml(match[1]);
        if (candidate.length >= 24) return candidate;
    }

    const withoutFences = markdown.replace(/```[\s\S]*?```/g, '');
    for (const block of withoutFences.split(/\n{2,}/)) {
        const candidate = block.trim();
        if (
            !candidate ||
            candidate.startsWith('#') ||
            candidate.startsWith('![') ||
            candidate.startsWith('<') ||
            candidate.startsWith('- ') ||
            candidate.startsWith('* ') ||
            /^\d+\.\s/.test(candidate)
        ) {
            continue;
        }
        const plain = candidate
            .replace(/\[([^\]]+)]\([^)]+\)/g, '$1')
            .replace(/[*_`>]/g, '')
            .replace(/\s+/g, ' ')
            .trim();
        if (plain.length >= 24) return plain;
    }
    return '';
}

function cleanReadmeBody(markdown: string): string {
    return markdown
        .split(/(```[\s\S]*?```)/g)
        .map((part) => {
            if (part.startsWith('```')) return part;
            return (
                stripUntilStable(part, /<!--[\s\S]*?-->/g, '')
                    // A marker can outlive the comment it belonged to: an
                    // unclosed `<!--`, or two fragments spliced into a new one
                    // by the removal above (`<!-` + `<!-- x -->` + `-`). Either
                    // way it is not markup anyone meant to publish.
                    .replace(/<!--|-->/g, '')
            )
                .replace(/<br\s*\/?>/gi, '\n')
                .replace(
                    /<\/?(?:a|b|center|details|div|em|img|kbd|p|picture|source|span|strong|sub|summary|sup|u)\b[^>]*>/gi,
                    '',
                );
        })
        .join('')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

export function resolveReadmeAssetUrl(
    value: string,
    owner: string,
    repo: string,
    branch: string,
): string {
    if (/^(?:https?:)?\/\//i.test(value) || value.startsWith('data:')) return value;
    const normalized = value.replace(/^\.\//, '').replace(/^\/+/, '');
    return `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${normalized}`;
}

export function resolveReadmeLinkUrl(
    value: string,
    owner: string,
    repo: string,
    branch: string,
): string {
    if (
        /^(?:https?:)?\/\//i.test(value) ||
        value.startsWith('#') ||
        value.startsWith('mailto:')
    ) {
        return value;
    }
    const normalized = value.replace(/^\.\//, '').replace(/^\/+/, '');
    return `https://github.com/${owner}/${repo}/blob/${branch}/${normalized}`;
}

export function extractReadmePresentation(
    markdown: string,
    repo: string,
): ReadmePresentation {
    const explicitTitle = markdown.match(/^#\s+(.+)$/m)?.[1]?.trim() || '';
    const coverImage =
        readmeImages(markdown).find((image) => !isDecorativeReadmeImage(image)) ?? null;
    const bodyWithPreamble = explicitTitle
        ? markdown.replace(/^#\s+.+(?:\r?\n)?/m, '').trim()
        : markdown.trim();
    const firstMarkdownHeading = bodyWithPreamble.search(/^#{1,6}\s+/m);
    const preambleEnd =
        firstMarkdownHeading >= 0 ? firstMarkdownHeading : bodyWithPreamble.length;
    const preamble = bodyWithPreamble
        .slice(0, preambleEnd)
        .replace(/<p\b[^>]*>[\s\S]*?<\/p>\s*/gi, '')
        .trim();
    const body = cleanReadmeBody(
        [preamble, bodyWithPreamble.slice(preambleEnd).trim()]
            .filter(Boolean)
            .join('\n\n'),
    );

    return {
        title: explicitTitle || humanizeRepositoryName(repo),
        intro: readmeIntro(markdown),
        coverImage,
        body,
    };
}

async function fetchRawReadme(owner: string, repo: string): Promise<string | null> {
    try {
        const response = await fetchWithTimeout(`https://api.github.com/repos/${owner}/${repo}/readme`, {
            headers: {
                Accept: 'application/vnd.github.raw+json',
            },
        });
        if (!response.ok) return null;
        return await response.text();
    } catch {
        return null;
    }
}

async function fetchRawFallback(owner: string, repo: string, branch: string): Promise<string | null> {
    try {
        const response = await fetchWithTimeout(
            `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/README.md`,
        );
        if (!response.ok) return null;
        return await response.text();
    } catch {
        return null;
    }
}

export async function fetchPublicGitHubReadme(
    owner: string,
    repo: string,
): Promise<PublicGitHubReadme | null> {
    const apiReadme = await fetchRawReadme(owner, repo);
    const branch = 'HEAD';
    const markdown =
        apiReadme ||
        (await fetchRawFallback(owner, repo, branch)) ||
        (await fetchRawFallback(owner, repo, 'main')) ||
        (await fetchRawFallback(owner, repo, 'master'));

    if (!markdown) return null;
    return { markdown, branch, repository: null };
}
