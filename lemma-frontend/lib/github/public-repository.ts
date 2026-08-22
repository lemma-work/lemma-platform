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
    /** The display width the README author declared for the cover, as a CSS
     *  length -- `"300px"`, or `"100%"` for a banner meant to fill the column.
     *  Null when they said nothing and the caller should pick. */
    coverMaxWidth: string | null;
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

interface ReadmeImage {
    src: string;
    maxWidth: string | null;
}

// A README author who writes `width="300"` on a phone screenshot has told us the
// size it is meant to be read at. Carrying that out of the parse is what lets the
// cover honour it instead of stretching a 300px image across a 1000px column.
//
// A percentage is the opposite instruction — `width="100%"` on a social banner
// asks it to fill whatever column it lands in — so it must not be read as a
// number. Matching leading digits alone turned that `100%` into a 100px cap and
// rendered a 1280px banner as a thumbnail. Anything we cannot resolve to a
// length here (em, vw) is treated as unsaid, and the caller's default applies.
function declaredImageWidth(tag: string): string | null {
    const attribute = tag.match(/\bwidth=["']?([^"'\s>]+)/i)?.[1];
    if (attribute) {
        if (/^\d+%$/.test(attribute)) return '100%';
        const pixels = attribute.match(/^(\d+)(?:px)?$/i);
        return pixels ? `${pixels[1]}px` : null;
    }
    const style = tag.match(/\bstyle=["'][^"']*\bmax-width:\s*(\d+)px/i)?.[1];
    return style ? `${style}px` : null;
}

function readmeImages(markdown: string): ReadmeImage[] {
    const images: ReadmeImage[] = [];
    for (const match of markdown.matchAll(/<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)) {
        if (match[1]) images.push({ src: match[1].trim(), maxWidth: declaredImageWidth(match[0]) });
    }
    for (const match of markdown.matchAll(/!\[[^\]]*]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/gi)) {
        if (match[1]) images.push({ src: match[1].trim(), maxWidth: null });
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
            // One looped alternation, in this order: a whole comment first,
            // then any marker that outlived one. HTML ends a comment with
            // `--!>` as well as `-->`, so both terminators count. Looping is
            // what makes it a strip rather than a pass -- removing a marker
            // can splice a new one out of its neighbours (`<` + `<!--` + `!--`
            // leaves `<!--`), and one pass would publish that.
            return stripUntilStable(part, /<!--[\s\S]*?--!?>|<!--|--!?>/g, '')
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

// `#` at the start of a line is a heading in prose and a comment in most shells,
// and a multiline regex cannot tell them apart. Searching the raw markdown made
// the first line of a ```bash block the page's <h1> -- one real README titled its
// page `set VITE_LEMMA_API_URL, VITE_LEMMA_AUTH_URL, VITE_LEMMA_POD_ID` -- and
// then deleted that line out of the install instructions it was quoting.
//
// Blanking the fence bodies in place, rather than removing them, is what keeps
// every offset equal to the original: the indices this returns are used to slice
// the *unmasked* string, so the two must stay the same length.
function maskFencedCode(markdown: string): string {
    return markdown.replace(
        // `(?![\s\S])`, not `$`, for the unterminated case: under `m` a bare `$`
        // matches the end of every line, so the lazy body would stop at the
        // first one and mask a single line of a long fence.
        /^([ \t]*)(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]*\2[^\n]*$|(?![\s\S]))/gm,
        (fence) => fence.replace(/[^\n]/g, ' '),
    );
}

export function extractReadmePresentation(
    markdown: string,
    repo: string,
): ReadmePresentation {
    // Every structural search runs against the masked copy; every slice and
    // replacement runs against the original.
    const masked = maskFencedCode(markdown);
    const markdownTitle = masked.match(/^#\s+(.+)$/m)?.[1]?.trim() || '';
    // A centred `<h1>` inside a `<p align="center">` header is the house style of
    // exactly the polished repositories this page exists to show off, and no `#`
    // heading follows it. Reading it is the difference between the repo's own
    // name and a guess made from its URL slug.
    const htmlTitle = markdownTitle
        ? ''
        : stripHtml(masked.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ?? '');
    const explicitTitle = markdownTitle || htmlTitle;
    const cover = readmeImages(markdown).find((image) => !isDecorativeReadmeImage(image.src));
    const coverImage = cover?.src ?? null;
    // Lift whichever element became the title out of the body — a title printed
    // twice is a mistake, and `cleanReadmeBody` keeps the text of tags it
    // strips, so an `<h1>` left behind surfaces as its own literal markup.
    // Addressed by where the *masked* copy found it, so the removal can never
    // reach inside a code fence.
    const titleIndex = markdownTitle ? masked.search(/^#\s+/m) : -1;
    const bodyWithPreamble = (
        titleIndex >= 0
            ? markdown.slice(0, titleIndex) +
              markdown.slice(titleIndex).replace(/^#\s+.+(?:\r?\n)?/, '')
            : htmlTitle
              ? markdown.replace(/<h1\b[^>]*>[\s\S]*?<\/h1>\s*/i, '')
              : markdown
    ).trim();
    const firstMarkdownHeading = maskFencedCode(bodyWithPreamble).search(/^#{1,6}\s+/m);
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
        coverMaxWidth: cover?.maxWidth ?? null,
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
