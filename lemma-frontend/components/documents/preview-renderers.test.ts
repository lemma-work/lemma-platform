import { describe, expect, it } from 'vitest';

import { pdfRenderPixelRatio } from './preview-renderers';

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
