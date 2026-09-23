import { describe, expect, it } from 'vitest';

import { cleanTriggerConfig } from './trigger-config';

describe('cleanTriggerConfig', () => {
    it('keeps what was filled in', () => {
        expect(cleanTriggerConfig({ repository_id: 1296269, actions: ['opened'] })).toEqual({
            repository_id: 1296269,
            actions: ['opened'],
        });
    });

    it('drops an untouched field rather than sending it empty', () => {
        // `repository_id: ''` is compared against a numeric repository id and
        // would match no repository at all — strictly worse than absent, which
        // means every repository in the installation.
        expect(cleanTriggerConfig({ repository_id: '', actions: [] })).toEqual({});
        expect(cleanTriggerConfig({ repository_id: '   ' })).toEqual({});
        expect(cleanTriggerConfig({ repository_id: undefined, actions: null as never })).toEqual({});
    });

    it('keeps a legitimately falsy value', () => {
        // `0` and `false` are answers, not blanks.
        expect(cleanTriggerConfig({ repository_id: 0, draft: false })).toEqual({
            repository_id: 0,
            draft: false,
        });
    });
});
