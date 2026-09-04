import { afterEach, describe, expect, it, vi } from 'vitest';

type RedirectsModule = typeof import('./redirects');

async function loadRedirects(env: Record<string, string | undefined> = {}): Promise<RedirectsModule> {
  vi.resetModules();
  process.env.NEXT_PUBLIC_SITE_URL = env.NEXT_PUBLIC_SITE_URL ?? 'https://app.lemma.work';
  process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX = env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX ?? '';
  return import('./redirects');
}

afterEach(() => {
  vi.resetModules();
  delete process.env.NEXT_PUBLIC_SITE_URL;
  delete process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX;
});

describe('normaliseRedirectUri', () => {
  it('allows relative and same-origin redirects', async () => {
    const { normaliseRedirectUri } = await loadRedirects();

    expect(normaliseRedirectUri('/pod/p1')).toBe('https://app.lemma.work/pod/p1');
    expect(normaliseRedirectUri('https://app.lemma.work/home')).toBe('https://app.lemma.work/home');
    // The site root, which is what Lemma Desktop sends on launch. The portal
    // continues an existing session only when it has somewhere to go, so a
    // null here is the difference between opening the workspace and stopping
    // on a "Continue" button every single launch.
    expect(normaliseRedirectUri('/')).toBe('https://app.lemma.work/');
  });

  it('rejects external redirects by default', async () => {
    const { normaliseRedirectUri } = await loadRedirects();

    expect(normaliseRedirectUri('https://evil.example/steal')).toBeNull();
    expect(normaliseRedirectUri('//evil.example/steal')).toBeNull();
  });

  it('allows configured app app subdomains without allowing lookalike hosts', async () => {
    const { normaliseRedirectUri } = await loadRedirects({
      NEXT_PUBLIC_APPS_DOMAIN_SUFFIX: 'apps.lemma.work',
    });

    expect(normaliseRedirectUri('https://sales.apps.lemma.work/app')).toBe(
      'https://sales.apps.lemma.work/app',
    );
    expect(normaliseRedirectUri('http://sales.apps.lemma.work/app')).toBeNull();
    expect(normaliseRedirectUri('https://evilapps.lemma.work/app')).toBeNull();
  });

  it('rejects auth-loop paths', async () => {
    const { normaliseRedirectUri } = await loadRedirects();

    expect(normaliseRedirectUri('/login')).toBeNull();
    expect(normaliseRedirectUri('/auth/callback')).toBeNull();
  });
});

describe('normaliseLoopbackRedirectUri', () => {
  it('allows loopback callbacks for CLI login', async () => {
    const { normaliseLoopbackRedirectUri } = await loadRedirects();

    expect(normaliseLoopbackRedirectUri('http://127.0.0.1:49152/callback')).toBe(
      'http://127.0.0.1:49152/callback',
    );
    expect(normaliseLoopbackRedirectUri('http://localhost:49152/callback')).toBe(
      'http://localhost:49152/callback',
    );
  });

  it('rejects non-loopback callbacks for CLI login', async () => {
    const { normaliseLoopbackRedirectUri } = await loadRedirects();

    expect(normaliseLoopbackRedirectUri('https://app.lemma.work/callback')).toBeNull();
    expect(normaliseLoopbackRedirectUri('https://evil.example/callback')).toBeNull();
  });
});
