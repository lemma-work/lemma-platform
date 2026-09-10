import { afterEach, describe, expect, it } from 'vitest';

import { crossSiteFramesCarryCookies } from '@/lib/desktop/local-capabilities';

// The bug this file exists for: a pod app embedded in the workspace loaded
// permanently signed out on macOS, and its SDK refreshed for ever trying to
// repair a session it could never store.
//
// `localhost` is not in the Public Suffix List, so WebKit cannot derive a
// registrable domain and treats `<slug>.apps.lemma.localhost` as a different
// *site* from `app.lemma.localhost`. Measured in a WKWebView harness, that
// iframe gets no storage at all: the server's Set-Cookie is not kept, a
// `document.cookie` write is silently dropped, a credentialed fetch answers
// 401, and `hasStorageAccess()` is false. No cookie attribute changes it.
//
// Chromium and WebView2 treat `*.localhost` as same-site, so this is macOS
// only -- which is exactly why it reproduced in the shipping app and nowhere
// else.

function pretend(platform: string | undefined, hostname: string): void {
    (globalThis as Record<string, unknown>).window = {
        __LEMMA_DESKTOP__: platform ? { version: '0', mode: 'local', platform } : undefined,
        location: { hostname },
    };
}

/** In the desktop shell, but talking to one too old to say which OS it is. */
function pretendShellWithoutPlatform(hostname: string): void {
    (globalThis as Record<string, unknown>).window = {
        __LEMMA_DESKTOP__: { version: '0', mode: 'local' },
        location: { hostname },
    };
}

afterEach(() => {
    delete (globalThis as Record<string, unknown>).window;
});

describe('whether an embedded app would still be signed in', () => {
    it('says no on macOS under .localhost, which is the broken case', () => {
        pretend('macos', 'app.lemma.localhost');
        expect(crossSiteFramesCarryCookies()).toBe(false);
    });

    it('says yes on macOS once the hostnames are a real registrable domain', () => {
        // Read from `location`, not from a flag, so moving the local hostnames
        // re-enables embedding on its own -- including the fallback path where
        // the real domain cannot be resolved and the install stays on
        // `.localhost`, which needs no coordination either.
        pretend('macos', 'app.lemma-local.example');
        expect(crossSiteFramesCarryCookies()).toBe(true);
    });

    it('says yes on the loopback wildcard this install actually serves', () => {
        // The example above proves the rule; this proves the host. `sslip.io`
        // is not itself a public suffix, so `127.0.0.1.sslip.io` is the
        // registrable domain and the workspace and its apps are same-site
        // under it. Named here because it is what a shipped install runs on,
        // and a rule that holds for a made-up example but not for the real
        // hostname would be found by a user rather than by this file.
        pretend('macos', 'app.127.0.0.1.sslip.io');
        expect(crossSiteFramesCarryCookies()).toBe(true);
    });

    it('says yes on Windows, where WebView2 treats *.localhost as same-site', () => {
        pretend('windows', 'app.lemma.localhost');
        expect(crossSiteFramesCarryCookies()).toBe(true);
    });

    it('fails closed inside a shell that never said which OS it is', () => {
        // `platform` is optional for a reason: locald serves a frontend pack
        // that updates independently of the shell, so a newer pack can run
        // inside an older shell that never injected it. Guessing "not macOS"
        // there restores the signed-out iframe on the one platform that has
        // the bug; guessing the other way costs a window on Windows.
        pretendShellWithoutPlatform('app.lemma.localhost');
        expect(crossSiteFramesCarryCookies()).toBe(false);
    });

    it('still permits embedding under a real domain when the OS is unknown', () => {
        // The unknown-platform case must not become a blanket refusal: the
        // hostname alone already settles it once the install has moved off
        // `.localhost`.
        pretendShellWithoutPlatform('app.127.0.0.1.sslip.io');
        expect(crossSiteFramesCarryCookies()).toBe(true);
    });

    it('says yes in a plain browser reaching the same local stack', () => {
        // A LAN browser or a public link has no desktop context at all, and
        // Chromium sends the cookie. Breaking embedding there would be a
        // regression bought for nothing.
        pretend(undefined, 'app.lemma.localhost');
        expect(crossSiteFramesCarryCookies()).toBe(true);
    });
});
