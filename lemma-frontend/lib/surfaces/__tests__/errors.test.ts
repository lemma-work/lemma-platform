import { describe, expect, it } from 'vitest';

import { parseCredentialConflict, surfaceErrorMessage } from '@/lib/surfaces/errors';

const conflictBody = {
    code: 'AGENT_SURFACE_CREDENTIAL_CONFLICT',
    message: 'System WHATSAPP credentials are already used by another surface.',
    details: {
        kind: 'SYSTEM',
        conflicting_surface: { pod_id: 'pod-7', name: 'whatsapp' },
    },
};

describe('surface error parsing', () => {
    it('reads a conflict off the generated client’s body', () => {
        const conflict = parseCredentialConflict({ body: conflictBody });
        expect(conflict).toEqual({
            kind: 'SYSTEM',
            podId: 'pod-7',
            surfaceName: 'whatsapp',
            message: conflictBody.message,
        });
    });

    it('reads the same conflict off a transport that names it `response`', () => {
        expect(parseCredentialConflict({ response: conflictBody })?.podId).toBe('pod-7');
    });

    it('distinguishes an account conflict from a shared-identity one', () => {
        // They read differently in the UI: one is "delete that surface", the
        // other is "your org already claimed the Lemma bot".
        const conflict = parseCredentialConflict({
            body: { ...conflictBody, details: { ...conflictBody.details, kind: 'ACCOUNT' } },
        });
        expect(conflict?.kind).toBe('ACCOUNT');
    });

    it('ignores errors that are not conflicts', () => {
        expect(parseCredentialConflict({ body: { code: 'AGENT_SURFACE_VALIDATION_ERROR' } })).toBeNull();
        expect(parseCredentialConflict(new Error('boom'))).toBeNull();
        expect(parseCredentialConflict(null)).toBeNull();
    });

    it('survives a conflict whose details never arrived', () => {
        const conflict = parseCredentialConflict({
            body: { code: 'AGENT_SURFACE_CREDENTIAL_CONFLICT' },
        });
        expect(conflict).toMatchObject({ kind: 'SYSTEM', podId: null, surfaceName: null });
        expect(conflict?.message).toBeTruthy();
    });

    describe('messages', () => {
        it('prefers the server’s own words', () => {
            expect(surfaceErrorMessage({ body: { message: 'Telegram said no' } }, 'fallback')).toBe(
                'Telegram said no',
            );
        });

        it('falls back to the thrown Error, then to the caller’s default', () => {
            expect(surfaceErrorMessage(new Error('network down'), 'fallback')).toBe('network down');
            expect(surfaceErrorMessage({}, 'fallback')).toBe('fallback');
            expect(surfaceErrorMessage(new Error('   '), 'fallback')).toBe('fallback');
        });
    });
});
