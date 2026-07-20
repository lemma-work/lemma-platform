import { describe, expect, it } from 'vitest';
import { RuntimeProfileScope } from 'lemma-sdk';

import { formatAddError, friendlyPodDefaultError } from './runtime-profile-toasts';

describe('formatAddError', () => {
    it('surfaces an org.update-specific hint when a Workspace add is blocked', () => {
        const msg = formatAddError(
            'GG Coder',
            RuntimeProfileScope.ORGANIZATION,
            'Missing permission org.update',
        );
        expect(msg).toContain('Workspace connections need org.update');
        expect(msg).toContain('ask an org admin to grant editor access');
        expect(msg).toContain('or choose Personal');
    });

    it('matches the org.update hint regardless of case on the wire', () => {
        const msg = formatAddError(
            'Claude Code',
            RuntimeProfileScope.ORGANIZATION,
            'Missing Permission Org.Update',
        );
        expect(msg).toContain('need org.update');
    });

    it('falls through to the raw message for non-org.update failures', () => {
        const msg = formatAddError(
            'Codex',
            RuntimeProfileScope.ORGANIZATION,
            'detected model names mismatch',
        );
        expect(msg).toBe("Couldn't add Codex: detected model names mismatch");
    });

    it('falls through to the raw message for Personal-scope failures', () => {
        // Personal scope shouldn't ever 403 on org.update, but if it does surface
        // a different error we want the raw message — not the org.update hint.
        const msg = formatAddError(
            'Cursor',
            RuntimeProfileScope.PERSONAL,
            'Missing permission org.update',
        );
        expect(msg).toBe("Couldn't add Cursor: Missing permission org.update");
    });

    it('handles a non-Error message ("Unknown error") without dropping the prefix', () => {
        const msg = formatAddError('OpenCode', RuntimeProfileScope.ORGANIZATION, 'Unknown error');
        expect(msg).toBe("Couldn't add OpenCode: Unknown error");
    });
});

describe('friendlyPodDefaultError', () => {
    it('collapses pod.update failures into "needs Pod Editor access"', () => {
        expect(friendlyPodDefaultError('Missing permission pod.update')).toBe(
            'needs Pod Editor access',
        );
    });

    it('collapses org.update failures into the same short hint', () => {
        expect(friendlyPodDefaultError('Missing permission org.update')).toBe(
            'needs Pod Editor access',
        );
    });

    it('returns the wire error unchanged for non-permission failures', () => {
        expect(friendlyPodDefaultError('timeout contacting pod controller')).toBe(
            'timeout contacting pod controller',
        );
    });
});
