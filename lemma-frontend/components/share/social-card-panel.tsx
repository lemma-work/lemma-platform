'use client';

import { useMemo, useState } from 'react';
import {
    Copy,
    Download,
    LinkedinLogo,
    Mail,
    RedditLogo,
    TelegramLogo,
    WhatsappLogo,
    XLogo,
    type LemmaIcon,
} from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
    socialCardFilename,
    socialCardPath,
    type SocialCardVariant,
} from '@/lib/share/social-card';
import {
    buildShareClipboardText,
    SHARE_TARGETS,
    type ShareSubject,
    type ShareTargetId,
} from '@/lib/share/share-targets';
import { cn } from '@/lib/utils';

/** Marks, not words — every destination then fits one row at any dialog width. */
const SHARE_TARGET_ICONS: Record<ShareTargetId, LemmaIcon> = {
    x: XLogo,
    linkedin: LinkedinLogo,
    whatsapp: WhatsappLogo,
    telegram: TelegramLogo,
    reddit: RedditLogo,
    email: Mail,
};

/** Every destination, as a mark, on one row. */
function ShareTargetRow({ subject }: { subject: ShareSubject }) {
    return (
        <div className="flex flex-wrap items-center gap-0.5">
            <span className="mr-1 text-xs text-[var(--text-tertiary)]">Post to</span>
            <TooltipProvider>
                {SHARE_TARGETS.map((target) => {
                    const Icon = SHARE_TARGET_ICONS[target.id];
                    return (
                        <Tooltip key={target.id}>
                            <TooltipTrigger asChild>
                                <Button asChild variant="ghost" size="icon" className="h-7 w-7">
                                    <a
                                        href={target.href(subject)}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        aria-label={target.action}
                                    >
                                        <Icon className="h-4 w-4" />
                                    </a>
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>{target.action}</TooltipContent>
                        </Tooltip>
                    );
                })}
            </TooltipProvider>
        </div>
    );
}

interface SocialCardPanelProps {
    variant: SocialCardVariant;
    name?: string | null;
    summary?: string | null;
    /** The link the card advertises. Also what every post intent sends. */
    url?: string | null;
    /** Small print under the card, e.g. the repository it came from. */
    label?: string | null;
    /**
     * `full` gives the card the whole width — right for a dedicated share
     * screen. `compact` keeps it to a thumbnail beside its actions, so it can
     * sit inside a dialog that is mostly about something else.
     */
    layout?: 'full' | 'compact';
    /**
     * Whether the shared URL actually serves Open Graph tags to a crawler.
     * Social platforms never accept an image through a share intent — they
     * fetch it from the link. A signed-in-only workspace URL has no crawlable
     * card, so the panel must say "attach this" instead of promising a preview
     * that will not appear.
     */
    unfurls?: boolean;
    className?: string;
}

/**
 * The picture that travels with the link.
 *
 * The preview, the PNG people copy, and the image a link unfurl renders are all
 * the same `/api/social-card` response — one renderer, so what someone sees
 * here is exactly what lands in the timeline.
 */
export function SocialCardPanel({
    variant,
    name,
    summary,
    url,
    label,
    layout = 'full',
    unfurls = false,
    className,
}: SocialCardPanelProps) {
    const [busy, setBusy] = useState<'copy' | 'download' | null>(null);

    const cardPath = useMemo(
        () =>
            socialCardPath({
                variant,
                title: name,
                detail: summary,
                label: label ?? url?.replace(/^https?:\/\//, '').replace(/\/$/, ''),
            }),
        [variant, name, summary, label, url],
    );

    const subject: ShareSubject | null = url ? { name, url, summary } : null;

    async function loadCard(): Promise<Blob> {
        const response = await fetch(cardPath);
        if (!response.ok) throw new Error('The share card could not be rendered.');
        return await response.blob();
    }

    async function copyImage() {
        if (busy) return;
        setBusy('copy');
        try {
            if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
                throw new Error('Image copy is unavailable in this browser.');
            }
            // Safari needs the promise handed to ClipboardItem synchronously,
            // inside the same user gesture, or it revokes clipboard permission.
            await navigator.clipboard.write([
                new ClipboardItem({ 'image/png': loadCard() }),
            ]);
            toast.success('Share card copied');
        } catch {
            try {
                await navigator.clipboard.writeText(
                    subject ? buildShareClipboardText(subject) : '',
                );
                toast.success('Post copied instead', {
                    description: 'This browser would not copy the image, so Lemma copied the text.',
                });
            } catch {
                toast.error('Could not copy the share card');
            }
        } finally {
            setBusy(null);
        }
    }

    async function downloadImage() {
        if (busy) return;
        setBusy('download');
        try {
            const blob = await loadCard();
            const objectUrl = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = socialCardFilename(name);
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(objectUrl);
            toast.success('Share card downloaded');
        } catch {
            toast.error('Could not download the share card');
        } finally {
            setBusy(null);
        }
    }

    const preview = (
        <div
            className={cn(
                'overflow-hidden rounded-md border border-[color:var(--border-subtle)] bg-[var(--surface-2)]',
                layout === 'compact' ? 'w-40 shrink-0 self-start' : 'rounded-lg',
            )}
        >
            {/* eslint-disable-next-line @next/next/no-img-element -- the card is a dynamic route response, not a static asset for the image optimizer. */}
            <img
                src={cardPath}
                alt={`Share card for ${name || 'this pod'}`}
                width={1200}
                height={630}
                className="block h-auto w-full"
            />
        </div>
    );

    if (layout === 'compact') {
        return (
            <div
                className={cn(
                    'flex items-start gap-3 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-3',
                    className,
                )}
            >
                {preview}
                <div className="min-w-0 flex-1 space-y-2">
                    <div>
                        <div className="text-sm font-medium text-[var(--text-primary)]">Share card</div>
                        <p className="text-xs text-[var(--text-tertiary)]">
                            {unfurls
                                ? 'The image people see when this link is posted.'
                                : 'Copy it into your post — a workspace link needs a sign-in, so it will not preview on its own.'}
                        </p>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <Button
                            type="button"
                            variant="secondary"
                            size="xs"
                            className="gap-1.5"
                            onClick={() => void copyImage()}
                            disabled={busy !== null}
                        >
                            <Copy className="h-3 w-3" />
                            {busy === 'copy' ? 'Copying…' : 'Copy image'}
                        </Button>
                        <Button
                            type="button"
                            variant="secondary"
                            size="xs"
                            className="gap-1.5"
                            onClick={() => void downloadImage()}
                            disabled={busy !== null}
                        >
                            <Download className="h-3 w-3" />
                            {busy === 'download' ? 'Preparing…' : 'PNG'}
                        </Button>
                    </div>

                    {subject ? <ShareTargetRow subject={subject} /> : null}
                </div>
            </div>
        );
    }

    return (
        <div className={cn('space-y-3', className)}>
            {preview}

            <div className="flex flex-wrap items-center gap-2">
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => void copyImage()}
                    disabled={busy !== null}
                >
                    <Copy className="h-3.5 w-3.5" />
                    {busy === 'copy' ? 'Copying…' : 'Copy image'}
                </Button>
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => void downloadImage()}
                    disabled={busy !== null}
                >
                    <Download className="h-3.5 w-3.5" />
                    {busy === 'download' ? 'Preparing…' : 'Download PNG'}
                </Button>

                {subject ? (
                    <div className="ml-auto">
                        <ShareTargetRow subject={subject} />
                    </div>
                ) : null}
            </div>
        </div>
    );
}
