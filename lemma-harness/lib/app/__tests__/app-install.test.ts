import { describe, expect, it } from 'vitest';

import { APP_INSTALL_REQUEST_MESSAGE, appInstallUrl } from '../app-install';

describe('appInstallUrl', () => {
    it('marks the app URL so the offer appears on arrival', () => {
        expect(appInstallUrl('https://invoices.apps.lemma.work')).toBe(
            'https://invoices.apps.lemma.work/#install',
        );
    });

    it('replaces a hash rather than appending to one', () => {
        expect(appInstallUrl('https://invoices.apps.lemma.work/#/reports')).toBe(
            'https://invoices.apps.lemma.work/#install',
        );
    });

    it('keeps the path and query the app was opened at', () => {
        expect(appInstallUrl('https://invoices.apps.lemma.work/q2?tab=open')).toBe(
            'https://invoices.apps.lemma.work/q2?tab=open#install',
        );
    });

    it('resolves without a window, because the app header renders on the server', () => {
        expect(typeof globalThis.window).toBe('undefined');
        expect(() => appInstallUrl('https://invoices.apps.lemma.work')).not.toThrow();
    });

    it('returns an unparseable link untouched rather than breaking it', () => {
        expect(appInstallUrl('not a url')).toBe('not a url');
    });

    it('names the message the injected script posts', () => {
        // Contract with `app/core/app_install.py`; changing one side alone
        // leaves the handoff silently dead.
        expect(APP_INSTALL_REQUEST_MESSAGE).toBe('lemma-app-install-request');
    });
});
