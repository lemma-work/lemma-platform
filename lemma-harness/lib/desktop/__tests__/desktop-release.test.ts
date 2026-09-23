import { describe, expect, it } from 'vitest';
import {
    DESKTOP_RELEASES_URL,
    formatInstallerSize,
    releaseVersion,
    selectDesktopBuilds,
} from '../desktop-release';

// A release carries far more than the app: host packs, guest runtimes, and a
// manifest, all named `lemma-*`. Handing one of those to someone who pressed
// "Download for macOS" is a file that cannot be opened, so the matching is
// deliberately exact rather than a suffix guess.
describe('desktop installers in a release', () => {
    const assets = [
        { name: 'lemma-local.json', browser_download_url: 'https://example.test/manifest', size: 900 },
        {
            name: 'lemma-host-pack-aarch64-apple-darwin.zip',
            browser_download_url: 'https://example.test/pack',
            size: 90_000_000,
        },
        {
            name: 'Lemma_0.7.0_aarch64-online.dmg',
            browser_download_url: 'https://example.test/mac.dmg',
            size: 24_100_000,
        },
        {
            name: 'Lemma_0.7.0_x64-online-setup.exe',
            browser_download_url: 'https://example.test/win.exe',
            size: 22_400_000,
        },
    ];

    it('picks one installer per platform, macOS first', () => {
        const builds = selectDesktopBuilds(assets);

        expect(builds.map((build) => build.platform)).toEqual(['macos', 'windows']);
        expect(builds[0].url).toBe('https://example.test/mac.dmg');
        expect(builds[1].url).toBe('https://example.test/win.exe');
    });

    it('offers nothing rather than a runtime archive', () => {
        expect(selectDesktopBuilds([assets[0], assets[1]])).toEqual([]);
    });

    it('skips a platform the release did not publish', () => {
        const builds = selectDesktopBuilds([assets[2]]);

        expect(builds).toHaveLength(1);
        expect(builds[0].platform).toBe('macos');
    });

    it('ignores an asset with no download link', () => {
        expect(selectDesktopBuilds([{ name: 'Lemma_0.7.0_aarch64-online.dmg' }])).toEqual([]);
    });

    // Everything published up to v0.6.2 is named this way. A matcher that only
    // knew the newer name would tell every visitor there is no Mac build.
    it('still finds the installers released before the variant suffix', () => {
        const builds = selectDesktopBuilds([
            { name: 'Lemma_0.6.2_aarch64.dmg', browser_download_url: 'https://example.test/old.dmg', size: 37_541_930 },
            { name: 'Lemma_0.6.2_x64-setup.exe', browser_download_url: 'https://example.test/old.exe' },
        ]);

        expect(builds.map((build) => build.url)).toEqual([
            'https://example.test/old.dmg',
            'https://example.test/old.exe',
        ]);
    });

    it('prefers the online build when a release carries both variants', () => {
        const builds = selectDesktopBuilds([
            { name: 'Lemma_0.7.0_aarch64-local.dmg', browser_download_url: 'https://example.test/local.dmg' },
            { name: 'Lemma_0.7.0_aarch64-online.dmg', browser_download_url: 'https://example.test/online.dmg' },
        ]);

        expect(builds).toHaveLength(1);
        expect(builds[0].url).toBe('https://example.test/online.dmg');
    });

    // A local-only build cannot reach a hosted workspace, and this page exists
    // to connect a computer to one.
    it('never offers a local-only build', () => {
        expect(
            selectDesktopBuilds([
                { name: 'Lemma_0.7.0_aarch64-local.dmg', browser_download_url: 'https://example.test/local.dmg' },
            ]),
        ).toEqual([]);
    });

    it('treats a missing size as unknown rather than zero', () => {
        const builds = selectDesktopBuilds([
            { name: 'Lemma_0.7.0_aarch64-online.dmg', browser_download_url: 'https://example.test/mac.dmg' },
        ]);

        expect(builds[0].size).toBeNull();
        expect(formatInstallerSize(builds[0].size)).toBeNull();
    });
});

describe('release presentation', () => {
    it('reads a version out of a tag', () => {
        expect(releaseVersion('v0.7.0')).toBe('0.7.0');
        expect(releaseVersion('0.7.0')).toBe('0.7.0');
        expect(releaseVersion('')).toBeNull();
        expect(releaseVersion(undefined)).toBeNull();
    });

    it('sizes an installer in the units a download dialog uses', () => {
        expect(formatInstallerSize(24_100_000)).toBe('24 MB');
        expect(formatInstallerSize(1_400_000_000)).toBe('1.4 GB');
        expect(formatInstallerSize(0)).toBeNull();
    });

    it('always has a page to fall back to', () => {
        expect(DESKTOP_RELEASES_URL).toContain('/releases/latest');
    });
});
