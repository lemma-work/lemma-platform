'use client';

import Link from 'next/link';
import { Bot, Check, ExternalLink, type LucideIcon, Radio, Share2, Wand2 } from 'lucide-react';

/** One of the four "make it yours" actions — a navigable card. `accent` gives
 * the flywheel's conversion step (Customize) a subtle pop. */
function RemixAction({
    href,
    icon: Icon,
    title,
    subtitle,
    accent = false,
}: {
    href: string;
    icon: LucideIcon;
    title: string;
    subtitle: string;
    accent?: boolean;
}) {
    return (
        <Link
            href={href}
            className={`rounded-lg border p-3.5 transition-colors hover:bg-[var(--surface-2)] ${
                accent ? 'border-[var(--accent)]' : 'border-[var(--border-subtle)]'
            }`}
        >
            <Icon className="h-5 w-5 text-[var(--accent)]" />
            <p className="mt-2 text-sm font-medium text-[var(--text-primary)]">{title}</p>
            <p className="text-xs text-[var(--text-tertiary)]">{subtitle}</p>
        </Link>
    );
}

/** The post-import celebration: the pod is live (fresh, or merged into one you
 * already had), now pivot the new owner into using, surfacing, sharing, and
 * customizing it (beat 5 of the loop). */
export function RemixTakeover({
    podId,
    podName,
    context = 'new',
    hasApp = false,
}: {
    podId: string;
    podName?: string;
    context?: 'new' | 'existing';
    hasApp?: boolean;
}) {
    return (
        <div>
            <div className="mb-1 flex items-center gap-2">
                <Check className="h-5 w-5 text-[var(--state-success)]" />
                <p className="text-base font-medium text-[var(--text-primary)]">
                    {context === 'existing'
                        ? `Imported into ${podName ?? 'your pod'}`
                        : `${podName ?? 'Your pod'} is live in your workspace`}
                </p>
            </div>
            <p className="mb-4 text-sm text-[var(--text-secondary)]">
                Make it yours — four ways in:
            </p>
            <div className="grid grid-cols-2 gap-2.5">
                {hasApp ? (
                    <RemixAction
                        href={`/pod/${podId}/app/pages`}
                        icon={ExternalLink}
                        title="View app"
                        subtitle="Open its pages and see it work"
                    />
                ) : (
                    <RemixAction
                        href={`/pod/${podId}/ai`}
                        icon={Bot}
                        title="Meet your agents"
                        subtitle="See what runs this pod"
                    />
                )}
                <RemixAction
                    href={`/pod/${podId}/surfaces`}
                    icon={Radio}
                    title="Activate a surface"
                    subtitle="Put it in Slack or web chat"
                />
                <RemixAction
                    href={`/pod/${podId}/import`}
                    icon={Share2}
                    title="Share with a friend"
                    subtitle="Publish to GitHub or send the bundle"
                />
                <RemixAction
                    href={`/pod/${podId}`}
                    icon={Wand2}
                    title="Customize"
                    subtitle="Chat with AI to make it truly yours"
                    accent
                />
            </div>
        </div>
    );
}
