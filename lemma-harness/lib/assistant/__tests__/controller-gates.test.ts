import { describe, expect, it } from 'vitest';

import { resolveAssistantControllerGates } from '@/lib/assistant/controller-gates';

describe('resolveAssistantControllerGates', () => {
    it('keeps commands enabled while automatic loading is dormant', () => {
        expect(resolveAssistantControllerGates(true, false)).toEqual({
            enabled: true,
            autoLoad: false,
        });
    });

    it('enables automatic loading after activation', () => {
        expect(resolveAssistantControllerGates(true, true)).toEqual({
            enabled: true,
            autoLoad: true,
        });
    });

    it('fully disables the controller with its provider', () => {
        expect(resolveAssistantControllerGates(false, true)).toEqual({
            enabled: false,
            autoLoad: false,
        });
    });
});
