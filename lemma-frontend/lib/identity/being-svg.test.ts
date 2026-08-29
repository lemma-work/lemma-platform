import { describe, expect, it } from 'vitest';

import { beingDataUri, renderBeingSvg } from './being-svg';
import { IDENTITY_TONES } from './identity-palette';
import { CRESTS, FORMS, identityGenes } from './seeded-identity';

const SEEDS = ['a1b2c3d4', 'support-triage', 'Ops Bot', '__lem__', 'ग्राहक'];

describe('a being, rendered outside the app', () => {
    it.each(SEEDS)('draws %s the same way every time', (seed) => {
        expect(renderBeingSvg({ seed })).toBe(renderBeingSvg({ seed }));
    });

    it('draws the body the component would have drawn', () => {
        const genes = identityGenes('a1b2c3d4');
        const svg = renderBeingSvg({ seed: 'a1b2c3d4' });
        expect(svg).toContain(FORMS[genes.form]);
        expect(svg).toContain(IDENTITY_TONES[genes.tone]);
    });

    /*
     * The whole reason this renderer exists. A `currentColor` or a `var(--…)`
     * surviving into the output is not a visual nit — there is no cascade where
     * this is looked at, so the shape it names renders black or not at all, and
     * the failure only shows up inside somebody's address book.
     */
    it.each(SEEDS)('leaves nothing for a stylesheet to resolve in %s', (seed) => {
        const svg = renderBeingSvg({ seed });
        expect(svg).not.toContain('currentColor');
        expect(svg).not.toContain('var(--');
    });

    it('resolves the paints inside a crest, which are the ones that hide', () => {
        // Find a seed whose crest is drawn at all — an empty crest would pass the
        // assertion above without ever exercising the substitution.
        const seed = SEEDS.find((candidate) => CRESTS[identityGenes(candidate).crest]);
        expect(seed, 'no fixture seed rolls a crest').toBeDefined();
        expect(CRESTS[identityGenes(seed!).crest]).toContain('currentColor');
        expect(renderBeingSvg({ seed: seed! })).not.toContain('currentColor');
    });

    it('paints a ground by default and drops it on request', () => {
        expect(renderBeingSvg({ seed: 'a1b2c3d4' })).toContain('<rect x="-2" y="-2"');
        expect(renderBeingSvg({ seed: 'a1b2c3d4', background: false })).not.toContain(
            '<rect x="-2" y="-2"',
        );
    });

    it('gives Lem its own reserved body', () => {
        expect(renderBeingSvg({ seed: '__lem__' })).toContain(FORMS[FORMS.length - 1]);
    });

    it('writes a size only when asked for one', () => {
        expect(renderBeingSvg({ seed: 'a1', size: 256 })).toContain('width="256" height="256"');
        expect(renderBeingSvg({ seed: 'a1' })).not.toContain('width="256"');
    });

    it('rounds the derived geometry instead of printing float noise', () => {
        expect(renderBeingSvg({ seed: 'a1b2c3d4' })).not.toMatch(/\d\.\d{4,}/);
    });

    it('encodes a data URI that survives its own hex colours', () => {
        const uri = beingDataUri({ seed: 'a1b2c3d4' });
        expect(uri.startsWith('data:image/svg+xml;base64,')).toBe(true);
        // A percent-encoded URI would have truncated at the first `#`.
        expect(atob(uri.slice('data:image/svg+xml;base64,'.length))).toBe(
            renderBeingSvg({ seed: 'a1b2c3d4' }),
        );
    });
});
