import { describe, expect, it } from 'vitest';

import { hasSkippedFirstPod, normalizeSkipOwner } from './onboarding-skip';

describe('hasSkippedFirstPod', () => {
    it('counts a skip recorded by this account', () => {
        expect(hasSkippedFirstPod('ada@example.com', 'ada@example.com')).toBe(true);
    });

    it('ignores case and padding on both sides', () => {
        expect(hasSkippedFirstPod('ada@example.com', '  Ada@Example.com ')).toBe(true);
    });

    it('ignores a skip recorded by a different account', () => {
        // The bug this exists for: one browser, several accounts, and a flag
        // that muted first-pod provisioning for everyone after the first.
        expect(hasSkippedFirstPod('someone-else@example.com', 'ada@example.com')).toBe(
            false,
        );
    });

    it('heals the legacy unowned "1" instead of trusting it', () => {
        expect(hasSkippedFirstPod('1', 'ada@example.com')).toBe(false);
    });

    it('is false when there is no flag or no signed-in address', () => {
        expect(hasSkippedFirstPod(null, 'ada@example.com')).toBe(false);
        expect(hasSkippedFirstPod('ada@example.com', null)).toBe(false);
        expect(hasSkippedFirstPod('ada@example.com', '   ')).toBe(false);
        expect(hasSkippedFirstPod(null, null)).toBe(false);
    });
});

describe('normalizeSkipOwner', () => {
    it('normalizes an address, and treats blank as absent', () => {
        expect(normalizeSkipOwner('  Ada@Example.com ')).toBe('ada@example.com');
        expect(normalizeSkipOwner('')).toBeNull();
        expect(normalizeSkipOwner('   ')).toBeNull();
        expect(normalizeSkipOwner(null)).toBeNull();
    });
});
