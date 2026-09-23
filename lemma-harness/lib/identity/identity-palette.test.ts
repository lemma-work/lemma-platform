import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
    IDENTITY_PUPIL,
    IDENTITY_SCLERA,
    IDENTITY_SHADE,
    IDENTITY_SOFTS,
    IDENTITY_STYLESHEET,
    IDENTITY_TONES,
    toneColor,
} from './identity-palette';
import { TONE_COUNT } from './seeded-identity';

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * The light ramp, read out of the stylesheet's first `:root` block.
 *
 * Scoped to that block deliberately: `.dark` redeclares every one of these
 * names, and a whole-file scan would happily match the dark value and call the
 * palette correct while a contact photo drew a violet meant for a black ground.
 */
function stylesheetLightRamp(): Map<string, string> {
    const css = readFileSync(join(frontendRoot, IDENTITY_STYLESHEET), 'utf8');
    const start = css.indexOf(':root {');
    expect(start, 'stylesheet has a :root block').toBeGreaterThanOrEqual(0);
    const end = css.indexOf('}', start);
    const block = css.slice(start, end);

    const ramp = new Map<string, string>();
    for (const [, name, value] of block.matchAll(/(--identity-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
        ramp.set(name, value.trim());
    }
    return ramp;
}

describe('identity palette', () => {
    const ramp = stylesheetLightRamp();

    it('carries one tone per gene the generator can roll', () => {
        expect(IDENTITY_TONES).toHaveLength(TONE_COUNT);
        expect(IDENTITY_SOFTS).toHaveLength(TONE_COUNT);
    });

    it.each(IDENTITY_TONES.map((color, tone) => ({ tone, color })))(
        'tone $tone matches the stylesheet',
        ({ tone, color }) => {
            expect(ramp.get(`--identity-tone-${tone}`)).toBe(color);
        },
    );

    it.each(IDENTITY_SOFTS.map((color, tone) => ({ tone, color })))(
        'soft $tone matches the stylesheet',
        ({ tone, color }) => {
            expect(ramp.get(`--identity-soft-${tone}`)).toBe(color);
        },
    );

    it('matches the stylesheet on the values shared by every tone', () => {
        expect(ramp.get('--identity-sclera')).toBe(IDENTITY_SCLERA);
        expect(ramp.get('--identity-pupil')).toBe(IDENTITY_PUPIL);
        expect(ramp.get('--identity-shade')).toBe(IDENTITY_SHADE);
    });

    it('never hands back nothing, whatever index it is asked for', () => {
        // `tone` is a rolled integer today, but a stored variant or a widened
        // TONE_COUNT could outrun the array, and a face with no fill is invisible
        // rather than obviously wrong.
        expect(toneColor(TONE_COUNT)).toBe(IDENTITY_TONES[0]);
        expect(toneColor(TONE_COUNT * 3 + 2)).toBe(IDENTITY_TONES[2]);
    });
});
