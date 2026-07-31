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

function decodeHtmlEntities(value: string): string {
    return value
        .replaceAll('&amp;', '&')
        .replaceAll('&lt;', '<')
        .replaceAll('&gt;', '>')
        .replaceAll('&quot;', '"')
        .replaceAll('&#39;', "'")
        .replaceAll('&nbsp;', ' ');
}

function stripHtml(value: string): string {
    return decodeHtmlEntities(value.replace(/<[^>]+>/g, ' '))
        .replace(/\s+/g, ' ')
        .trim();
}

function humanizeRepositoryName(value: string): string {
    return value
        .replace(/[-_]+/g, ' ')
        .replace(/\b\w/g, (letter) => letter.toUpperCase())
        .trim();
}

function isDecorativeReadmeImage(value: string): boolean {
    const source = value.toLowerCase();
    return (
        source.includes('shields.io') ||
        source.includes('/badge') ||
        source.includes('install-remix-on-lemma') ||
        source.includes('github.com/lemma-work/lemma-platform/actions/')
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
            return part
                .replace(/<!--[\s\S]*?-->/g, '')
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
