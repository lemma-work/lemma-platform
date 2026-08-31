import { describe, expect, it } from 'vitest';

import {
    canPrintDocument,
    getDocumentPreviewType,
    isTextPreviewType,
    pdfRenderPixelRatio,
    printFileName,
} from './preview-renderers';

describe('pdfRenderPixelRatio', () => {
    it('supersamples PDFs even on a 1x display', () => {
        expect(pdfRenderPixelRatio(1)).toBe(1.5);
    });

    it('matches common high-density displays and caps excessive allocation', () => {
        expect(pdfRenderPixelRatio(2)).toBe(2);
        expect(pdfRenderPixelRatio(4)).toBe(2.5);
    });

    it('uses a safe fallback for invalid ratios', () => {
        expect(pdfRenderPixelRatio(Number.NaN)).toBe(1.5);
    });
});

describe('canPrintDocument', () => {
    it('prints the prose the viewer typeset itself', () => {
        expect(canPrintDocument('markdown')).toBe(true);
    });

    it('leaves a PDF to Download', () => {
        // The file is already the artefact; re-printing a rasterised page
        // through the browser hands over something worse than the bytes.
        expect(canPrintDocument('pdf')).toBe(false);
    });

    it('stays away from previews the page cannot lay out', () => {
        // An HTML preview lives in a sandboxed iframe, and office files and
        // images are rendered from opaque blobs.
        expect(canPrintDocument('html')).toBe(false);
        expect(canPrintDocument('office')).toBe(false);
        expect(canPrintDocument('image')).toBe(false);
        expect(canPrintDocument('code')).toBe(false);
        expect(canPrintDocument('json')).toBe(false);
        expect(canPrintDocument('unsupported')).toBe(false);
    });
});

describe('printFileName', () => {
    it('drops the extension the browser is about to replace', () => {
        // Otherwise the saved copy is `Zapdata_Proposal.md.pdf`.
        expect(printFileName('Zapdata_Proposal.md')).toBe('Zapdata_Proposal');
    });

    it('keeps a name that has no extension', () => {
        expect(printFileName('CHANGELOG')).toBe('CHANGELOG');
    });

    it('drops only the last extension', () => {
        expect(printFileName('notes.2026.md')).toBe('notes.2026');
    });

    it('never empties a dotfile', () => {
        // The leading dot opens the name rather than closing it, so treating it
        // as an extension would save the file with no name at all.
        expect(printFileName('.gitignore')).toBe('.gitignore');
    });

    it('ignores surrounding whitespace', () => {
        expect(printFileName('  Proposal.md  ')).toBe('Proposal');
    });
});

describe('getDocumentPreviewType', () => {
    it('reads markdown by either extension', () => {
        expect(getDocumentPreviewType('/docs/proposal.md')).toBe('markdown');
        expect(getDocumentPreviewType('/docs/proposal.markdown')).toBe('markdown');
    });

    it('ignores case', () => {
        expect(getDocumentPreviewType('/Docs/Deep/Zapdata_Proposal.MD')).toBe('markdown');
    });

    it('names the types a shared file arrives as', () => {
        // The share route used to sniff the blob's MIME type instead, which put
        // every one of these through the same `<pre>`: a page shown as its own
        // markup, prose shown as its asterisks, a spreadsheet shown as nothing.
        expect(getDocumentPreviewType('/site/index.html')).toBe('html');
        expect(getDocumentPreviewType('/site/index.htm')).toBe('html');
        expect(getDocumentPreviewType('/data/rows.csv')).toBe('code');
        expect(getDocumentPreviewType('/data/config.json')).toBe('json');
        expect(getDocumentPreviewType('/img/diagram.svg')).toBe('image');
        expect(getDocumentPreviewType('/decks/q3.pptx')).toBe('office');
        expect(getDocumentPreviewType('/contracts/msa.pdf')).toBe('pdf');
    });

    it('has no opinion about a file it cannot show', () => {
        expect(getDocumentPreviewType('/archive/backup.zip')).toBe('unsupported');
    });
});

describe('isTextPreviewType', () => {
    it('covers the types whose stored form is what a person reads', () => {
        expect(isTextPreviewType('markdown')).toBe(true);
        expect(isTextPreviewType('json')).toBe(true);
        expect(isTextPreviewType('html')).toBe(true);
        expect(isTextPreviewType('code')).toBe(true);
    });

    it('excludes the ones that have to be rendered from bytes', () => {
        expect(isTextPreviewType('pdf')).toBe(false);
        expect(isTextPreviewType('image')).toBe(false);
        expect(isTextPreviewType('office')).toBe(false);
        expect(isTextPreviewType('unsupported')).toBe(false);
    });
});
