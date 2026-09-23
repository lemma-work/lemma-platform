import { describe, expect, it } from 'vitest';

import {
    MAX_PINNED_CONVERSATIONS,
    parsePinnedIds,
    pinnedStorageKey,
    togglePinnedId,
    withoutPinnedId,
} from './pinned-conversations';

describe('pinnedStorageKey', () => {
    it('separates people as well as pods', () => {
        // One machine, two accounts: the previous person's pins are not this
        // person's, and neither are the next pod's.
        expect(pinnedStorageKey('user-a', 'pod-1')).not.toBe(pinnedStorageKey('user-b', 'pod-1'));
        expect(pinnedStorageKey('user-a', 'pod-1')).not.toBe(pinnedStorageKey('user-a', 'pod-2'));
    });
});

describe('parsePinnedIds', () => {
    it('reads back what was written', () => {
        expect(parsePinnedIds(JSON.stringify(['a', 'b']))).toEqual(['a', 'b']);
    });

    it('treats anything unreadable as nothing pinned', () => {
        // This runs during a render. Whatever is in the key — nothing, broken
        // JSON, an older shape — has to come back as a list, never a throw.
        expect(parsePinnedIds(null)).toEqual([]);
        expect(parsePinnedIds('')).toEqual([]);
        expect(parsePinnedIds('{')).toEqual([]);
        expect(parsePinnedIds('"a"')).toEqual([]);
        expect(parsePinnedIds(JSON.stringify({ a: 1 }))).toEqual([]);
    });

    it('drops entries that are not usable ids', () => {
        expect(parsePinnedIds(JSON.stringify(['a', 42, null, '  ', 'b', 'a']))).toEqual(['a', 'b']);
    });

    it('never returns more than the cap', () => {
        const stored = Array.from({ length: MAX_PINNED_CONVERSATIONS + 5 }, (_, i) => `id-${i}`);
        expect(parsePinnedIds(JSON.stringify(stored))).toHaveLength(MAX_PINNED_CONVERSATIONS);
    });
});

describe('togglePinnedId', () => {
    it('pins to the front and unpins in place', () => {
        expect(togglePinnedId(['a'], 'b')).toEqual(['b', 'a']);
        expect(togglePinnedId(['b', 'a'], 'b')).toEqual(['a']);
    });

    it('drops the oldest pin at the cap', () => {
        const full = Array.from({ length: MAX_PINNED_CONVERSATIONS }, (_, i) => `id-${i}`);
        const next = togglePinnedId(full, 'newest');

        expect(next).toHaveLength(MAX_PINNED_CONVERSATIONS);
        expect(next[0]).toBe('newest');
        expect(next).not.toContain(`id-${MAX_PINNED_CONVERSATIONS - 1}`);
    });
});

describe('withoutPinnedId', () => {
    it('is a no-op for something that was never pinned', () => {
        expect(withoutPinnedId(['a'], 'b')).toEqual(['a']);
    });
});
