/**
 * Turning a stored file into something a browser can show.
 *
 * An HTML file in a pod is rarely one file: it links a stylesheet, it points at
 * images, and both live beside it in the datastore under paths no iframe can
 * resolve on its own. So every relative reference is followed, fetched through
 * whatever read the caller is allowed, and inlined as a data URL — the preview
 * that results is a single self-contained document, which is also the only kind
 * a sandboxed frame can render without reaching back into the app's origin.
 *
 * Lifted out of the viewer so the share route renders a shared `.html` exactly
 * the way the workspace does. An asset the reader may not read simply fails to
 * resolve and is left alone: preview degrades, nothing leaks.
 */

export type HtmlPreviewDocument = {
    srcDoc: string;
};

/**
 * The page a rendered .docx is displayed on.
 *
 * `docx-preview` hands back the document's own stylesheet alongside its markup,
 * so this adds almost nothing to it — the styling that matters came out of the
 * file. What is here is the frame around the pages: the backdrop they sit on,
 * and a way for a page whose width Word fixed in inches to survive a preview
 * pane narrower than that.
 *
 * The frame is loaded into a `sandbox=""` iframe, so nothing in it can script,
 * navigate or reach this origin. The policy below closes the one door sandboxing
 * leaves open: a document that names an external stylesheet, font or image would
 * otherwise announce to that host that someone had opened the file. Images and
 * fonts are inlined by the renderer, so `data:` is all either needs.
 */
export function buildDocxPreviewSrcDoc(contentHtml: string, documentStyles = ''): string {
    return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'" />
  ${documentStyles}
  <style>
    :root { color-scheme: light; }
    html, body { margin: 0; padding: 0; min-height: 100%; }
    body { background: rgb(241 245 249); overflow-x: auto; }

    /* The renderer paints its own flat grey backdrop and a hard drop shadow.
       Both are replaced rather than removed: the pages still need to read as
       paper laid on something, just not as a 2004 print dialog. */
    .docx-wrapper { background: transparent; padding: 24px 16px; }
    .docx-wrapper > section.docx {
      box-shadow: 0 1px 2px rgb(15 23 42 / 0.10), 0 8px 24px rgb(15 23 42 / 0.08);
      margin-bottom: 24px;
    }

    /* A Word page is a fixed width in inches — around 816px for Letter — and a
       preview pane is whatever the reader left it at. Scaling the whole page
       keeps the layout the document specified instead of reflowing it into
       something Word never described. The frame is sandboxed, so this is done
       in steps rather than measured: no script runs in here to measure with. */
    @media (max-width: 860px) { .docx-wrapper { zoom: 0.9; } }
    @media (max-width: 780px) { .docx-wrapper { zoom: 0.8; } }
    @media (max-width: 680px) { .docx-wrapper { zoom: 0.7; } }
    @media (max-width: 600px) { .docx-wrapper { zoom: 0.6; } }
    @media (max-width: 520px) { .docx-wrapper { zoom: 0.5; } }
  </style>
</head>
<body>${contentHtml}</body>
</html>`;
}

function looksLikeHtmlDocument(contentHtml: string): boolean {
    return /^\s*<!doctype\s+html/i.test(contentHtml) || /^\s*<html[\s>]/i.test(contentHtml);
}

export function buildHtmlPreviewSrcDoc(contentHtml: string): string {
    if (looksLikeHtmlDocument(contentHtml)) {
        return contentHtml;
    }

    return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
</head>
<body>${contentHtml}</body>
</html>`;
}

function getDirectoryPath(filePath: string): string {
    const normalized = filePath.replace(/\\/g, '/');
    const lastSlashIndex = normalized.lastIndexOf('/');
    if (lastSlashIndex <= 0) return '/';
    return normalized.slice(0, lastSlashIndex);
}

function normalizePathSegments(path: string): string {
    const startsWithSlash = path.startsWith('/');
    const segments = path.split('/').filter((segment) => segment.length > 0);
    const normalizedSegments: string[] = [];

    segments.forEach((segment) => {
        if (segment === '.') return;
        if (segment === '..') {
            normalizedSegments.pop();
            return;
        }
        normalizedSegments.push(segment);
    });

    return `${startsWithSlash ? '/' : ''}${normalizedSegments.join('/')}` || '/';
}

export function resolveHtmlAssetPath(baseFilePath: string, rawUrl: string): string | null {
    const value = rawUrl.trim();
    if (!value || value.startsWith('#')) return null;
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value)) return null;

    const [withoutHash] = value.split('#');
    const [pathname] = withoutHash.split('?');
    if (!pathname) return null;
    const decodedPathname = safeDecodeUrlPath(pathname);

    if (decodedPathname.startsWith('/')) {
        return normalizePathSegments(decodedPathname);
    }

    return normalizePathSegments(`${getDirectoryPath(baseFilePath)}/${decodedPathname}`);
}

function safeDecodeUrlPath(pathname: string): string {
    try {
        return decodeURIComponent(pathname);
    } catch {
        return pathname;
    }
}

function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            if (typeof reader.result === 'string') resolve(reader.result);
            else reject(new Error('Failed to read asset'));
        };
        reader.onerror = () => reject(reader.error || new Error('Failed to read asset'));
        reader.readAsDataURL(blob);
    });
}

export async function buildHtmlPreviewDocument({
    contentHtml,
    documentPath,
    loadAsset,
}: {
    contentHtml: string;
    documentPath: string;
    loadAsset: (path: string) => Promise<Blob>;
}): Promise<HtmlPreviewDocument> {
    const html = buildHtmlPreviewSrcDoc(contentHtml);
    const parser = new DOMParser();
    const parsed = parser.parseFromString(html, 'text/html');
    const assetCache = new Map<string, Promise<string | null>>();

    const resolveAssetUrl = (rawUrl: string, basePath = documentPath): Promise<string | null> => {
        const assetPath = resolveHtmlAssetPath(basePath, rawUrl);
        if (!assetPath) return Promise.resolve(null);

        const cached = assetCache.get(assetPath);
        if (cached) return cached;

        const next = loadAsset(assetPath)
            .then((blob) => blobToDataUrl(blob))
            .catch(() => null);
        assetCache.set(assetPath, next);
        return next;
    };

    const resolveStylesheetUrl = (rawUrl: string): Promise<string | null> => {
        const stylesheetPath = resolveHtmlAssetPath(documentPath, rawUrl);
        if (!stylesheetPath) return Promise.resolve(null);

        const cacheKey = `css:${stylesheetPath}`;
        const cached = assetCache.get(cacheKey);
        if (cached) return cached;

        const next = loadAsset(stylesheetPath)
            .then(async (blob) => {
                const cssText = await blob.text();
                const rewrittenCss = await rewriteCssUrls(cssText, stylesheetPath, resolveAssetUrl);
                return blobToDataUrl(new Blob([rewrittenCss], { type: blob.type || 'text/css' }));
            })
            .catch(() => resolveAssetUrl(rawUrl));
        assetCache.set(cacheKey, next);
        return next;
    };

    const rewriteAttribute = async (
        selector: string,
        attribute: string,
        resolveUrl: (rawUrl: string) => Promise<string | null> = resolveAssetUrl
    ) => {
        const elements = Array.from(parsed.querySelectorAll<HTMLElement>(selector));
        await Promise.all(elements.map(async (element) => {
            const rawValue = element.getAttribute(attribute);
            if (!rawValue) return;
            const nextValue = await resolveUrl(rawValue);
            if (nextValue) element.setAttribute(attribute, nextValue);
        }));
    };

    const rewriteSrcset = async () => {
        const elements = Array.from(parsed.querySelectorAll<HTMLElement>('[srcset]'));
        await Promise.all(elements.map(async (element) => {
            const rawValue = element.getAttribute('srcset');
            if (!rawValue) return;
            const nextValue = await rewriteSrcsetValue(rawValue, resolveAssetUrl);
            if (nextValue) element.setAttribute('srcset', nextValue);
        }));
    };

    await Promise.all([
        rewriteAttribute('img[src], script[src], iframe[src], source[src], video[src], audio[src], embed[src]', 'src'),
        rewriteAttribute('object[data]', 'data'),
        rewriteAttribute('link[rel~="stylesheet"][href]', 'href', resolveStylesheetUrl),
        rewriteAttribute('link[rel~="icon"][href], link[rel~="preload"][href], link[rel~="modulepreload"][href]', 'href'),
        rewriteAttribute('[poster]', 'poster'),
        rewriteSrcset(),
    ]);

    return {
        srcDoc: `<!doctype html>\n${parsed.documentElement.outerHTML}`,
    };
}

async function rewriteCssUrls(
    cssText: string,
    stylesheetPath: string,
    resolveAssetUrl: (rawUrl: string, basePath?: string) => Promise<string | null>
): Promise<string> {
    const urlPattern = /url\(\s*(["']?)([^"')]+)\1\s*\)/gi;
    const matches = Array.from(cssText.matchAll(urlPattern));
    if (matches.length === 0) return cssText;

    const replacements = await Promise.all(matches.map(async (match) => {
        const rawUrl = match[2]?.trim();
        if (!rawUrl) return null;
        const objectUrl = await resolveAssetUrl(rawUrl, stylesheetPath);
        return objectUrl ? { from: match[0], to: `url("${objectUrl}")` } : null;
    }));

    return replacements.reduce((nextCss, replacement) => (
        replacement ? nextCss.replace(replacement.from, replacement.to) : nextCss
    ), cssText);
}

async function rewriteSrcsetValue(
    srcset: string,
    resolveAssetUrl: (rawUrl: string) => Promise<string | null>
): Promise<string | null> {
    const candidates = srcset.split(',').map((candidate) => candidate.trim()).filter(Boolean);
    if (candidates.length === 0) return null;

    const rewrittenCandidates = await Promise.all(candidates.map(async (candidate) => {
        const [rawUrl, ...descriptorParts] = candidate.split(/\s+/);
        if (!rawUrl) return candidate;
        const objectUrl = await resolveAssetUrl(rawUrl);
        return [objectUrl || rawUrl, ...descriptorParts].join(' ');
    }));

    return rewrittenCandidates.join(', ');
}
