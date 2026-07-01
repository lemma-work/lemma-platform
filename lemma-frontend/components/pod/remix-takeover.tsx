'use client';

import Link from 'next/link';
import { Check, ExternalLink, type LucideIcon, Radio, Share2, Wand2 } from 'lucide-react';

/** One of the four "make it yours" actions — a navigable card. */
function RemixAction({
    href,
    icon: Icon,
    title,
    subtitle,
}: {
    href: string;
    icon: LucideIcon;
    title: string;
    subtitle: string;
}) {
    return (
        <Link
            href={href}
            className="rounded-lg border border-[var(--border-subtle)] p-3.5 transition-colors hover:bg-[var(--surface-2)]"
        >
            <Icon className="h-5 w-5 text-[var(--accent)]" />
            <p className="mt-2 text-sm font-medium text-[var(--text-primary)]">{title}</p>
            <p className="text-xs text-[var(--text-tertiary)]">{subtitle}</p>
        </Link>
    );
}

/** The post-import celebration: a fresh pod is live, now pivot the new owner
 * into using, surfacing, sharing, and customizing it (beat 5 of the loop). */
export function RemixTakeover({ podId, podName }: { podId: string; podName?: string }) {
    return (
        <div>
            <div className="mb-1 flex items-center gap-2">
                <Check className="h-5 w-5 text-[var(--state-success)]" />
                <p className="text-base font-medium text-[var(--text-primary)]">
                    {podName ?? 'Your pod'} is live in your workspace
                </p>
            </div>
            <p className="mb-4 text-sm text-[var(--text-secondary)]">Make it yours.</p>
            <div className="grid grid-cols-2 gap-2.5">
                <RemixAction
                    href={`/pod/${podId}/app`}
                    icon={ExternalLink}
                    title="View app"
                    subtitle="Open the app"
                />
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
                    subtitle="Get a link or download"
                />
                <RemixAction
                    href={`/pod/${podId}`}
                    icon={Wand2}
                    title="Customize"
                    subtitle="Rename, tweak agents, swap data"
                />
            </div>
        </div>
    );
}
