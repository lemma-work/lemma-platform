export type DocumentPreviewType = 'markdown' | 'json' | 'html' | 'code' | 'image' | 'pdf' | 'office' | 'unsupported';

export interface PdfPreviewPage {
    src: string;
    displayWidth: number;
    displayHeight: number;
}

export interface PdfPreviewData {
    pages: PdfPreviewPage[];
    totalPages: number;
    truncated: boolean;
}

const MAX_PDF_PREVIEW_PAGES = 24;
const PDF_DISPLAY_SCALE = 1.25;
const MIN_PDF_RENDER_PIXEL_RATIO = 1.5;
const MAX_PDF_RENDER_PIXEL_RATIO = 2.5;
const CODE_FILE_EXTENSIONS = new Set([
    'asc',
    'asm',
    'astro',
    'bash',
    'bat',
    'c',
    'cc',
    'cfg',
    'clj',
    'cljs',
    'cljc',
    'cmd',
    'cjs',
    'conf',
    'cpp',
    'cts',
    'cs',
    'css',
    'csv',
    'cxx',
    'dart',
    'dockerignore',
    'edn',
    'env',
    'erb',
    'erl',
    'ex',
    'exs',
    'fish',
    'gemspec',
    'go',
    'gradle',
    'graphql',
    'gql',
    'groovy',
    'h',
    'hh',
    'hpp',
    'hrl',
    'hs',
    'hxx',
    'ini',
    'java',
    'jl',
    'js',
    'json5',
    'jsonc',
    'jsx',
    'kt',
    'kts',
    'less',
    'log',
    'lua',
    'm',
    'make',
    'mdx',
    'mk',
    'mm',
    'mjs',
    'mts',
    'nim',
    'php',
    'pl',
    'pm',
    'properties',
    'proto',
    'ps1',
    'psm1',
    'py',
    'r',
    'rb',
    'rego',
    'rs',
    'sass',
    'scala',
    'scss',
    'sh',
    'sol',
    'sql',
    'styl',
    'svelte',
    'swift',
    'tcl',
    'tf',
    'tfvars',
    'toml',
    'ts',
    'tsv',
    'tsx',
    'txt',
    'vb',
    'vue',
    'xml',
    'xsd',
    'xsl',
    'yaml',
    'yml',
    'zig',
    'zsh',
]);
const CODE_FILE_BASENAMES = new Set([
    '.bashrc',
    '.editorconfig',
    '.env',
    '.envrc',
    '.env.development',
    '.env.local',
    '.env.production',
    '.env.test',
    '.gitignore',
    '.gitmodules',
    '.npmrc',
    '.prettierignore',
    '.prettierrc',
    '.prettierrc.json',
    '.prettierrc.yaml',
    '.prettierrc.yml',
    '.zshrc',
    'dockerfile',
    'gemfile',
    'justfile',
    'makefile',
    'procfile',
    'readme',
    'readme.md',
]);
const IMAGE_FILE_EXTENSIONS = new Set([
    'apng',
    'avif',
    'bmp',
    'gif',
    'ico',
    'jpeg',
    'jpg',
    'png',
    'svg',
    'webp',
]);

export type OfficePreviewKind = 'docx' | 'other';

export interface DocxPreviewData {
    html: string;
    warnings: string[];
}

export function getDocumentPreviewType(filePath: string): DocumentPreviewType {
    const lowerPath = filePath.toLowerCase();
    const fileName = lowerPath.split('/').pop() || lowerPath;
    const extensionIndex = fileName.lastIndexOf('.');
    const extension = extensionIndex >= 0 ? fileName.slice(extensionIndex + 1) : '';

    if (lowerPath.endsWith('.md') || lowerPath.endsWith('.markdown')) return 'markdown';
    if (lowerPath.endsWith('.json')) return 'json';
    if (lowerPath.endsWith('.html') || lowerPath.endsWith('.htm')) return 'html';
    if (lowerPath.endsWith('.pdf')) return 'pdf';
    if (IMAGE_FILE_EXTENSIONS.has(extension)) return 'image';
    if (
        lowerPath.endsWith('.doc')
        || lowerPath.endsWith('.docx')
        || lowerPath.endsWith('.ppt')
        || lowerPath.endsWith('.pptx')
        || lowerPath.endsWith('.xls')
        || lowerPath.endsWith('.xlsx')
    ) return 'office';
    if (CODE_FILE_BASENAMES.has(fileName)) return 'code';
    if (CODE_FILE_EXTENSIONS.has(extension)) return 'code';

    return 'unsupported';
}

/**
 * Whether this file is read as text rather than as bytes.
 *
 * The four types whose stored form *is* the thing a person edits — which is
 * also, and not coincidentally, the set the viewer can save.
 */
export function isTextPreviewType(previewType: DocumentPreviewType): boolean {
    return previewType === 'markdown'
        || previewType === 'json'
        || previewType === 'html'
        || previewType === 'code';
}

/**
 * Whether this document has a printable page behind it.
 *
 * Markdown only, and the restraint is the point. Print here means "lay the
 * rendered page out on paper", which is a thing the viewer can honestly do for
 * prose it typeset itself. A PDF is already the artefact — Download hands over
 * the real bytes rather than a browser's re-print of a rasterised page. Office
 * files and images are the same story, and an HTML preview lives in a sandboxed
 * iframe the parent page cannot lay out at all.
 */
export function canPrintDocument(previewType: DocumentPreviewType): boolean {
    return previewType === 'markdown';
}

/**
 * What a printed copy of this document should be called.
 *
 * Browsers seed the "Save as PDF" filename from the page title, so without this
 * a proposal saves as whatever the route was named. The extension goes because
 * the browser appends its own — `Zapdata_Proposal.md.pdf` reads like a mistake.
 */
export function printFileName(documentName: string): string {
    const trimmed = documentName.trim();
    const lastDot = trimmed.lastIndexOf('.');

    // `<= 0` covers both "no extension" and a leading dot, where the dot opens a
    // hidden file's name rather than closing one — `.gitignore` must not print
    // as the empty string.
    if (lastDot <= 0) return trimmed;
    return trimmed.slice(0, lastDot);
}

export function getOfficePreviewKind(filePath: string): OfficePreviewKind {
    const lowerPath = filePath.toLowerCase();
    if (lowerPath.endsWith('.docx')) return 'docx';
    return 'other';
}

export async function renderDocxPreview(blob: Blob): Promise<DocxPreviewData> {
    const mammoth = await import('mammoth');
    const arrayBuffer = await blob.arrayBuffer();
    const result = await mammoth.convertToHtml({ arrayBuffer });
    const warnings = Array.isArray(result.messages)
        ? result.messages
            .map((message) => (typeof message.message === 'string' ? message.message.trim() : ''))
            .filter((message) => message.length > 0)
        : [];

    return {
        html: result.value || '',
        warnings,
    };
}

export function pdfRenderPixelRatio(devicePixelRatio?: number): number {
    const ratio = typeof devicePixelRatio === 'number' && Number.isFinite(devicePixelRatio)
        ? devicePixelRatio
        : 1;
    return Math.min(
        MAX_PDF_RENDER_PIXEL_RATIO,
        Math.max(MIN_PDF_RENDER_PIXEL_RATIO, ratio),
    );
}

export async function renderPdfPreview(blob: Blob): Promise<PdfPreviewData> {
    const pdfjs = await import('pdfjs-dist');

    if (!pdfjs.GlobalWorkerOptions.workerSrc) {
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
            'pdfjs-dist/build/pdf.worker.min.mjs',
            import.meta.url
        ).toString();
    }

    const bytes = new Uint8Array(await blob.arrayBuffer());
    const loadingTask = pdfjs.getDocument({ data: bytes });
    const pdf = await loadingTask.promise;

    try {
        const totalPages = pdf.numPages;
        const targetPages = Math.min(totalPages, MAX_PDF_PREVIEW_PAGES);
        const pages: PdfPreviewPage[] = [];
        const renderPixelRatio = pdfRenderPixelRatio(
            typeof window === 'undefined' ? undefined : window.devicePixelRatio,
        );

        for (let pageIndex = 1; pageIndex <= targetPages; pageIndex += 1) {
            const page = await pdf.getPage(pageIndex);
            const displayViewport = page.getViewport({ scale: PDF_DISPLAY_SCALE });
            const renderViewport = page.getViewport({
                scale: PDF_DISPLAY_SCALE * renderPixelRatio,
            });
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            if (!context) continue;

            canvas.width = Math.ceil(renderViewport.width);
            canvas.height = Math.ceil(renderViewport.height);

            await page.render({
                canvas,
                canvasContext: context,
                viewport: renderViewport,
            }).promise;
            pages.push({
                src: canvas.toDataURL('image/png'),
                displayWidth: Math.ceil(displayViewport.width),
                displayHeight: Math.ceil(displayViewport.height),
            });
        }

        return {
            pages,
            totalPages,
            truncated: totalPages > targetPages,
        };
    } finally {
        await pdf.destroy();
    }
}
