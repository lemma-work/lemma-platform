/**
 * Where the Lemma desktop app is downloaded from.
 *
 * Installer names carry their version — `Lemma_0.7.0_aarch64-online.dmg` — so
 * GitHub's `releases/latest/download/<name>` shortcut cannot address one
 * without knowing the version first. That is the whole reason this reads the
 * release rather than hardcoding a link: a hardcoded one goes stale on every
 * release, and a stale installer link is worse than no link, because it looks
 * like it worked.
 *
 * Every failure lands on `releasesUrl`, which is a page a person can act on.
 */

export const DESKTOP_REPOSITORY = 'lemma-work/lemma-platform';

export const DESKTOP_RELEASES_URL = `https://github.com/${DESKTOP_REPOSITORY}/releases/latest`;

export type DesktopPlatform = 'macos' | 'windows';

export type DesktopBuild = {
    platform: DesktopPlatform;
    /** What the person is choosing between, not the file name. */
    label: string;
    /** What it needs to run, stated next to the build it applies to. */
    requirement: string;
    /** Direct link to the installer. */
    url: string;
    /** Bytes, or null when the release did not report a size. */
    size: number | null;
};

export type DesktopRelease = {
    /** Tag of the release these builds came from, or null when unresolved. */
    version: string | null;
    builds: DesktopBuild[];
    releasesUrl: string;
};

export type ReleaseAsset = {
    name?: unknown;
    browser_download_url?: unknown;
    size?: unknown;
};

// One installer per platform, chosen by the first pattern that matches
// something.
//
// The suffix moved: releases up to v0.6.2 published `Lemma_<v>_aarch64.dmg`,
// and later ones name the variant. Both are accepted so this page keeps working
// across that boundary in either direction — a matcher that only knew the new
// name would show "no installers" against every already-published release.
//
// Order is preference, not convenience: `-online` first because someone reading
// this arrived from a hosted workspace, and the unsuffixed name second because
// that is what the older releases called the same build. A `-local` asset
// deliberately matches neither — it cannot connect to a hosted workspace, so
// offering it here would be a download that cannot do the thing being asked for.
const BUILDS: {
    platform: DesktopPlatform;
    label: string;
    requirement: string;
    patterns: RegExp[];
}[] = [
    {
        platform: 'macos',
        label: 'macOS · Apple silicon',
        requirement: 'macOS 14 or later',
        patterns: [/^Lemma_.+_aarch64-online\.dmg$/, /^Lemma_.+_aarch64\.dmg$/],
    },
    {
        platform: 'windows',
        label: 'Windows · x64',
        requirement: 'Windows 10 or later',
        patterns: [/^Lemma_.+_x64-online-setup\.exe$/, /^Lemma_.+_x64-setup\.exe$/],
    },
];

/**
 * The installers a release published, in a fixed platform order.
 *
 * Anything unrecognised is dropped rather than guessed at: the release also
 * carries host packs, guest runtimes and a manifest, and offering one of those
 * as "the app" would be a download that cannot be opened.
 */
export function selectDesktopBuilds(assets: readonly ReleaseAsset[]): DesktopBuild[] {
    const builds: DesktopBuild[] = [];
    for (const { platform, label, requirement, patterns } of BUILDS) {
        let match: ReleaseAsset | undefined;
        for (const pattern of patterns) {
            match = assets.find(
                (asset) =>
                    typeof asset?.name === 'string' &&
                    pattern.test(asset.name) &&
                    typeof asset.browser_download_url === 'string',
            );
            if (match) break;
        }
        if (!match) continue;
        builds.push({
            platform,
            label,
            requirement,
            url: match.browser_download_url as string,
            size: typeof match.size === 'number' && match.size > 0 ? match.size : null,
        });
    }
    return builds;
}

/** `0.7.0` from a `v0.7.0` tag, and whatever it is otherwise. */
export function releaseVersion(tag: unknown): string | null {
    if (typeof tag !== 'string' || !tag.trim()) return null;
    return tag.trim().replace(/^v/, '');
}

/** `241.6 MB`, or null when the release did not say. */
export function formatInstallerSize(bytes: number | null): string | null {
    if (!bytes || bytes <= 0) return null;
    const megabytes = bytes / 1_000_000;
    return megabytes >= 1000
        ? `${(megabytes / 1000).toFixed(1)} GB`
        : `${megabytes.toFixed(0)} MB`;
}

/**
 * Read the latest desktop release.
 *
 * Server-side and cached for an hour: unauthenticated GitHub allows 60 reads an
 * hour per address, and a download page that rate-limits itself under load
 * would fail exactly when it is busiest.
 */
export async function fetchLatestDesktopRelease(): Promise<DesktopRelease> {
    const empty: DesktopRelease = {
        version: null,
        builds: [],
        releasesUrl: DESKTOP_RELEASES_URL,
    };
    try {
        const response = await fetch(
            `https://api.github.com/repos/${DESKTOP_REPOSITORY}/releases/latest`,
            {
                headers: { Accept: 'application/vnd.github+json' },
                next: { revalidate: 3600 },
            },
        );
        if (!response.ok) return empty;
        const release = (await response.json()) as {
            tag_name?: unknown;
            assets?: unknown;
        };
        const assets = Array.isArray(release.assets) ? (release.assets as ReleaseAsset[]) : [];
        return {
            version: releaseVersion(release.tag_name),
            builds: selectDesktopBuilds(assets),
            releasesUrl: DESKTOP_RELEASES_URL,
        };
    } catch {
        // Network, DNS, or a shape we do not recognise. The page still renders
        // and still sends people somewhere they can download from.
        return empty;
    }
}
