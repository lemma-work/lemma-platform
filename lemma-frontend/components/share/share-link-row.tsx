'use client';

import { useState } from 'react';
import { Check, Copy, Link2, Share2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';
import { shareSubject, type ShareSubject } from '@/lib/share/share-targets';
import { cn } from '@/lib/utils';

interface ShareLinkRowProps {
    url?: string | null;
    /** Name of the thing being shared — used for the native share sheet copy. */
    name?: string | null;
    summary?: string | null;
    /** Shown in place of the link when there is nothing to copy yet. */
    emptyHint?: string;
    /** Offers the OS share sheet alongside copy. Off for internal-only links. */
    allowNativeShare?: boolean;
    className?: string;
}

/**
 * The link, in the open.
 *
 * Copying the link is the most common thing anyone does in a share dialog, so
 * it gets a field of its own at the top rather than a button hidden in the
 * footer. Showing the URL also answers the question people actually have —
 * "what will they land on?" — before they send it to anyone.
 */
export function ShareLinkRow({
    url,
    name,
    summary,
    emptyHint = 'A link appears once this is saved.',
    allowNativeShare = false,
    className,
}: ShareLinkRowProps) {
    const [copied, setCopied] = useState(false);
    const canShare =
        allowNativeShare &&
        typeof navigator !== 'undefined' &&
        typeof navigator.share === 'function';

    const subject: ShareSubject | null = url ? { name, url, summary } : null;

    async function copyLink() {
        if (!url) return;
        try {
            await navigator.clipboard.writeText(url);
            setCopied(true);
            playSoundFeedback('action-success');
            window.setTimeout(() => setCopied(false), 1600);
        } catch {
            toast.error('Could not copy the link');
        }
    }

    async function nativeShare() {
        if (!subject) return;
        const result = await shareSubject(subject);
        if (result === 'copied') toast.success('Link copied');
        if (result === 'failed') toast.error('Could not share this link');
    }

    return (
        <div
            className={cn(
                'flex items-center gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-2)] p-1.5 pl-3',
                className,
            )}
        >
            <Link2
                className={cn(
                    'h-4 w-4 shrink-0',
                    url ? 'text-[var(--text-tertiary)]' : 'text-[var(--text-soft)]',
                )}
            />
            <span
                className={cn(
                    'min-w-0 flex-1 truncate text-sm',
                    url ? 'text-[var(--text-secondary)]' : 'text-[var(--text-tertiary)]',
                )}
                title={url ?? undefined}
            >
                {url ?? emptyHint}
            </span>

            {canShare ? (
                <TooltipProvider>
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 shrink-0"
                                onClick={() => void nativeShare()}
                                aria-label="Open the share sheet"
                            >
                                <Share2 className="h-4 w-4" />
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>Share via…</TooltipContent>
                    </Tooltip>
                </TooltipProvider>
            ) : null}

            <Button
                type="button"
                variant="secondary"
                size="sm"
                className="shrink-0 gap-1.5"
                onClick={() => void copyLink()}
                disabled={!url}
            >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? 'Copied' : 'Copy'}
            </Button>
        </div>
    );
}
