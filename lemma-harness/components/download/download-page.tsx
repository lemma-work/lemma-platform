'use client';

import Link from 'next/link';
import { useSyncExternalStore } from 'react';

import { Logo } from '@/components/brand/logo';
import { Button } from '@/components/ui/button';
import { ArrowLeft, ArrowUpRight, Download } from '@/components/ui/icons';
import {
    formatInstallerSize,
    type DesktopBuild,
    type DesktopPlatform,
    type DesktopRelease,
} from '@/lib/desktop/desktop-release';
import { cn } from '@/lib/utils';

function subscribeToNothing() {
    return () => {};
}

/**
 * Which installer this visitor most likely wants.
 *
 * A guess, and treated as one: it only decides which button is emphasised and
 * which is listed second. Both are always reachable, because someone reading
 * this on a phone is downloading for a computer that is not in their hand.
 */
function detectPlatform(): DesktopPlatform | null {
    if (typeof navigator === 'undefined') return null;
    const agent = navigator.userAgent;
    if (/Mac/i.test(agent)) return 'macos';
    if (/Win/i.test(agent)) return 'windows';
    return null;
}

function usePlatform(): DesktopPlatform | null {
    // Server-rendered as "unknown", so the markup the server sends never claims
    // a platform it cannot know and then rewrites itself on hydration.
    return useSyncExternalStore(subscribeToNothing, detectPlatform, () => null);
}

function BuildRow({ build, emphasised }: { build: DesktopBuild; emphasised: boolean }) {
    const size = formatInstallerSize(build.size);

    return (
        <div
            className={cn(
                'flex flex-wrap items-center justify-between gap-3 rounded-md border p-4',
                emphasised
                    ? 'border-[var(--border-strong)] bg-[var(--surface-1)]'
                    : 'border-[var(--border-subtle)]',
            )}
        >
            <div className="min-w-0">
                <div className="text-sm font-medium text-[var(--text-primary)]">{build.label}</div>
                <p className="mt-1 text-sm text-[var(--text-tertiary)]">
                    {size ? `${build.requirement} · ${size}` : build.requirement}
                </p>
            </div>
            <Button asChild variant={emphasised ? 'primary' : 'secondary'} size="sm" className="gap-1.5">
                <a href={build.url}>
                    <Download className="size-3.5" />
                    Download
                </a>
            </Button>
        </div>
    );
}

export function DownloadPage({ release }: { release: DesktopRelease }) {
    const platform = usePlatform();
    // The detected platform first, so the button someone wants is the one they
    // read first — and it is the only `primary` on the page.
    const builds = [...release.builds].sort((left, right) => {
        if (left.platform === right.platform) return 0;
        if (left.platform === platform) return -1;
        if (right.platform === platform) return 1;
        return 0;
    });

    return (
        <main className="min-h-screen bg-[var(--bg-canvas)] text-[var(--text-primary)]">
            <div className="sticky top-0 z-20 border-b border-[var(--row-border)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_84%,transparent)] backdrop-blur-xl">
                <div className="mx-auto flex max-w-[1180px] items-center justify-between gap-4 px-5 py-4 sm:px-8 lg:px-10">
                    <Link href="/" aria-label="Lemma home" className="inline-flex items-center">
                        <Logo size="xs" variant="mark-wordmark" />
                    </Link>
                    <Link
                        href="/"
                        className="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                    >
                        <ArrowLeft className="h-4 w-4" />
                        <span>Back to home</span>
                    </Link>
                </div>
            </div>

            <section className="px-5 pt-12 sm:px-8 sm:pt-16 lg:px-10 lg:pt-20">
                <div className="mx-auto max-w-[1180px]">
                    <div className="max-w-[640px]">
                        <p className="font-mono type-eyebrow-medium">Lemma for desktop</p>
                        <h1 className="mt-5 [font-family:var(--font-landing-serif)] text-5xl font-normal leading-none tracking-normal text-[var(--text-primary)] sm:text-6xl">
                            Run agents on your own computer
                        </h1>
                        <p className="mt-6 text-base leading-8 text-[var(--text-secondary)] sm:text-lg">
                            Claude Code, Codex and OpenCode already live on your machine, holding your
                            credentials and seeing your files. The Lemma app connects that computer to
                            your workspace so agents there can pick up work, without those credentials
                            ever leaving it.
                        </p>
                    </div>
                </div>
            </section>

            {/* The download comes before the instructions on a narrow screen and
                beside them on a wide one, so a phone does not make someone scroll
                past the whole pitch to reach the button they came for. */}
            <section className="px-5 pb-16 pt-10 sm:px-8 lg:px-10">
                <div className="mx-auto grid max-w-[1180px] gap-10 lg:grid-cols-[minmax(0,1fr)_400px] lg:gap-16">
                    <div className="order-2 max-w-[640px] lg:order-none">
                        <ol className="space-y-5">
                            {[
                                'Install the app on the computer whose agents you want to use.',
                                'Open it and sign in to the same workspace.',
                                'Pick the agents you want under Models → Computers.',
                            ].map((step, index) => (
                                <li key={step} className="flex items-baseline gap-4">
                                    <span className="min-w-6 font-mono text-xs text-[var(--text-tertiary)]">
                                        {(index + 1).toString().padStart(2, '0')}
                                    </span>
                                    <span className="text-base leading-7 text-[var(--text-secondary)]">
                                        {step}
                                    </span>
                                </li>
                            ))}
                        </ol>

                        <p className="mt-10 text-sm leading-7 text-[var(--text-tertiary)]">
                            The same app also installs a complete Lemma on your machine, if you would
                            rather your workspace itself did not live in the cloud. You choose which on
                            first launch, and can change it later.
                        </p>
                    </div>

                    <aside className="order-1 lg:order-none lg:sticky lg:top-[88px] lg:self-start">
                        <div className="surface-panel p-5">
                            <div className="flex items-baseline justify-between gap-3">
                                <h2 className="text-sm font-medium text-[var(--text-primary)]">
                                    Download
                                </h2>
                                {release.version ? (
                                    <span className="font-mono text-xs text-[var(--text-tertiary)]">
                                        v{release.version}
                                    </span>
                                ) : null}
                            </div>

                            {builds.length ? (
                                <div className="mt-4 flex flex-col gap-3">
                                    {builds.map((build, index) => (
                                        <BuildRow
                                            key={build.platform}
                                            build={build}
                                            // Nothing is emphasised until the platform is known, so
                                            // the page never pushes a Windows installer at a Mac.
                                            emphasised={platform !== null && index === 0}
                                        />
                                    ))}
                                </div>
                            ) : (
                                <p className="mt-4 text-sm leading-7 text-[var(--text-secondary)]">
                                    The installer list could not be read just now. Every build is on
                                    the releases page.
                                </p>
                            )}

                            <a
                                href={release.releasesUrl}
                                className="mt-4 inline-flex items-center gap-1 text-sm text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                            >
                                <span>All releases and checksums</span>
                                <ArrowUpRight className="h-3.5 w-3.5" />
                            </a>

                            {builds.length ? (
                                <p className="mt-5 border-t border-[var(--row-border)] pt-4 text-xs leading-6 text-[var(--text-tertiary)]">
                                    Signed by Folks and Machines, Inc., and notarised by Apple on
                                    macOS.
                                </p>
                            ) : null}
                        </div>
                    </aside>
                </div>
            </section>
        </main>
    );
}
