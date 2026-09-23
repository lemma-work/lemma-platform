import { describe, expect, it } from 'vitest';

import { buildDocxPreviewSrcDoc, buildHtmlPreviewSrcDoc, resolveHtmlAssetPath } from './html-preview';

describe('buildHtmlPreviewSrcDoc', () => {
    it('leaves a whole document alone', () => {
        const page = '<!doctype html>\n<html><body>Hi</body></html>';
        expect(buildHtmlPreviewSrcDoc(page)).toBe(page);
    });

    it('recognises a document however it was typed', () => {
        // Authors write `<!DOCTYPE html>`, generators write `<!doctype html>`,
        // and a hand-written page may open at `<html>` with no doctype at all.
        expect(buildHtmlPreviewSrcDoc('<!DOCTYPE HTML><html><body>x</body></html>'))
            .toContain('<!DOCTYPE HTML>');
        expect(buildHtmlPreviewSrcDoc('  <html lang="en"><body>x</body></html>'))
            .toBe('  <html lang="en"><body>x</body></html>');
    });

    it('wraps a fragment so it renders as a page', () => {
        const wrapped = buildHtmlPreviewSrcDoc('<h1>Notes</h1>');
        expect(wrapped).toContain('<!doctype html>');
        expect(wrapped).toContain('<body><h1>Notes</h1></body>');
    });
});

describe('buildDocxPreviewSrcDoc', () => {
    it('carries the document\'s own stylesheet into the frame', () => {
        // The whole reason a .docx looks like itself here: the renderer hands
        // back Word's styles alongside the markup, and dropping them on the
        // floor would still produce a page — just the wrong one.
        const page = buildDocxPreviewSrcDoc(
            '<div class="docx-wrapper"><section class="docx">Hi</section></div>',
            '<style>.docx p { font-family: Cambria; }</style>',
        );
        expect(page).toContain('<style>.docx p { font-family: Cambria; }</style>');
        expect(page).toContain('<body><div class="docx-wrapper">');
    });

    it('stops a document announcing that it was opened', () => {
        // Sandboxing the frame kills script and origin access but not a plain
        // fetch, so a stylesheet or image URL left in the file would still tell
        // its host who read the document and when.
        const page = buildDocxPreviewSrcDoc('<p>x</p>');
        expect(page).toContain("default-src 'none'");
        expect(page).toContain('img-src data:');
    });

    it('renders a document that brought no styles', () => {
        expect(buildDocxPreviewSrcDoc('<p>x</p>')).toContain('<body><p>x</p></body>');
    });
});

describe('resolveHtmlAssetPath', () => {
    it('resolves a sibling against the document it was linked from', () => {
        expect(resolveHtmlAssetPath('/site/index.html', 'style.css')).toBe('/site/style.css');
    });

    it('walks up out of the document directory', () => {
        expect(resolveHtmlAssetPath('/site/pages/about.html', '../assets/logo.png'))
            .toBe('/site/assets/logo.png');
    });

    it('never climbs above the datastore root', () => {
        // `..` past the top is a traversal attempt, not a path. It collapses
        // rather than producing something outside the pod's files.
        expect(resolveHtmlAssetPath('/site/index.html', '../../../../etc/passwd'))
            .toBe('/etc/passwd');
    });

    it('takes an absolute reference as datastore-absolute', () => {
        expect(resolveHtmlAssetPath('/site/deep/page.html', '/shared/base.css'))
            .toBe('/shared/base.css');
    });

    it('leaves anything that is not a datastore file alone', () => {
        // Returning null means "do not rewrite" — a CDN script, an inline data
        // URL and a protocol-relative host all keep whatever they already had.
        expect(resolveHtmlAssetPath('/site/index.html', 'https://cdn.example.com/a.js')).toBeNull();
        expect(resolveHtmlAssetPath('/site/index.html', 'data:image/png;base64,AAAA')).toBeNull();
        expect(resolveHtmlAssetPath('/site/index.html', '//cdn.example.com/a.js')).toBeNull();
        expect(resolveHtmlAssetPath('/site/index.html', '#section')).toBeNull();
        expect(resolveHtmlAssetPath('/site/index.html', '   ')).toBeNull();
    });

    it('drops the query and fragment a browser would not send to storage', () => {
        expect(resolveHtmlAssetPath('/site/index.html', 'app.js?v=3')).toBe('/site/app.js');
        expect(resolveHtmlAssetPath('/site/index.html', 'icons.svg#pin')).toBe('/site/icons.svg');
    });

    it('reads a percent-encoded name back to the name on disk', () => {
        expect(resolveHtmlAssetPath('/site/index.html', 'Q3%20report.png'))
            .toBe('/site/Q3 report.png');
    });

    it('keeps a name that only looks percent-encoded', () => {
        // A stray `%` is a legal character in a filename, and decoding throws on
        // it — the raw name is the better guess.
        expect(resolveHtmlAssetPath('/site/index.html', '100%.png')).toBe('/site/100%.png');
    });
});
