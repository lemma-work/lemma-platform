/**
 * These cases are the backend's, not this module's: each one is a branch of
 * `normalize_pod_name`, so the copy can be shown to still agree with the rule
 * it mirrors rather than only with itself.
 */

import { describe, expect, it } from 'vitest';

import { normalizePodName, podNameError } from './pod-name';

describe('podNameError', () => {
    it('accepts the names pods are actually given', () => {
        expect(podNameError('Acme')).toBeNull();
        expect(podNameError('Customer support')).toBeNull();
        expect(podNameError('invoice-triage')).toBeNull();
        expect(podNameError('team_2')).toBeNull();
    });

    it('rejects an empty name, before and after trimming', () => {
        expect(podNameError('')).toBe('Pod name cannot be empty');
        expect(podNameError('   ')).toBe('Pod name cannot be empty');
    });

    it('rejects punctuation the server will not store', () => {
        // The apostrophe is the one people reach for first — "Team's Pod" is
        // the exact name the backend's own e2e test rejects.
        expect(podNameError("Team's Pod")).toBe(
            'Pod name may contain only letters, numbers, spaces, hyphens, and underscores',
        );
        expect(podNameError('acme.co')).not.toBeNull();
        expect(podNameError('acme@lemma')).not.toBeNull();
    });

    it('rejects a name that does not start and end alphanumeric', () => {
        expect(podNameError('-acme')).not.toBeNull();
        expect(podNameError('acme-')).not.toBeNull();
        expect(podNameError('_acme_')).not.toBeNull();
    });

    it('measures length after trimming, as the server does', () => {
        const longest = 'a'.repeat(255);
        expect(podNameError(longest)).toBeNull();
        expect(podNameError(`  ${longest}  `)).toBeNull();
        expect(podNameError(`${longest}b`)).toBe('Pod name must be 255 characters or fewer');
    });

    it('judges the trimmed name, so surrounding space is not an error', () => {
        expect(podNameError('  Acme  ')).toBeNull();
    });
});

describe('normalizePodName', () => {
    it('trims, and leaves the inside alone', () => {
        expect(normalizePodName('  Acme  ')).toBe('Acme');
        // Interior spacing is the name the person chose; the server keeps it.
        expect(normalizePodName('Acme  Support')).toBe('Acme  Support');
    });
});
