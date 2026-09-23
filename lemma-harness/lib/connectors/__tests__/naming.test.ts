import { describe, expect, it } from 'vitest';

import { suggestInstallName } from '@/lib/connectors/naming';

describe('naming a second install of one connector', () => {
    it('leaves the first install to the backend default', () => {
        expect(suggestInstallName('slack', [])).toBe('');
        expect(suggestInstallName('slack', ['gmail', 'notion'])).toBe('');
    });

    // The bug this exists for: a second Slack app — one agent's own bot —
    // saved with a blank name and collided with the first install.
    it('suggests a free name once the bare one is taken', () => {
        expect(suggestInstallName('slack', ['slack'])).toBe('slack-2');
        expect(suggestInstallName('slack', ['slack', 'slack-2'])).toBe('slack-3');
    });

    it('ignores the case an existing install was named in', () => {
        expect(suggestInstallName('slack', ['Slack'])).toBe('slack-2');
    });

    it('has nothing to suggest without a connector', () => {
        expect(suggestInstallName(undefined, ['slack'])).toBe('');
    });
});
