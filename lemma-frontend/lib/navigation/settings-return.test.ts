import { describe, expect, it } from 'vitest';

import {
    normalizeInternalReturnPath,
    withSettingsReturnPath,
} from './settings-return';

describe('settings return navigation', () => {
    it('preserves a pod-local path with its query and hash', () => {
        expect(normalizeInternalReturnPath('/pod/pod-1/conversations/new?agent=builder#composer'))
            .toBe('/pod/pod-1/conversations/new?agent=builder#composer');
    });

    it('rejects external and protocol-relative destinations', () => {
        expect(normalizeInternalReturnPath('https://example.com/steal')).toBeNull();
        expect(normalizeInternalReturnPath('//example.com/steal')).toBeNull();
        expect(normalizeInternalReturnPath('javascript:alert(1)')).toBeNull();
    });

    it('adds a return path without discarding existing settings query parameters', () => {
        expect(withSettingsReturnPath(
            '/organizations/org-1/settings/agent-runtimes?tab=local',
            '/pod/pod-1/conversations/new?agent=builder',
        )).toBe(
            '/organizations/org-1/settings/agent-runtimes?tab=local&returnTo=%2Fpod%2Fpod-1%2Fconversations%2Fnew%3Fagent%3Dbuilder',
        );
    });
});
