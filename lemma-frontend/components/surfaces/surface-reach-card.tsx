'use client';

import { useState } from 'react';
import QRCode from 'react-qr-code';
import { Check, Copy, ExternalLink } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { getSurfaceDeepLink, getSurfaceIdentity } from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

/**
 * How a human reaches this surface — the proof that setup worked.
 *
 * Every connect journey ends here rather than on a toast, because the handle and
 * a link to it are the only things that let someone confirm the agent is
 * actually reachable. Platforms with no direct-open convention (Slack, Teams,
 * mailboxes) get the handle without the QR.
 */
export function SurfaceReachCard({ surface }: { surface: AssistantSurface }) {
    const [copied, setCopied] = useState(false);
    const handle = surface.reach?.handle || getSurfaceIdentity(surface) || surface.reach?.email || null;
    const deepLink = getSurfaceDeepLink(surface);

    if (!handle && !deepLink) return null;

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(deepLink || handle || '');
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Could not copy to clipboard');
        }
    };

    return (
        <div className="surface-reach-card">
            {deepLink ? (
                <div className="surface-reach-qr" aria-hidden>
                    <QRCode value={deepLink} size={96} bgColor="transparent" fgColor="currentColor" />
                </div>
            ) : null}

            <div className="min-w-0 flex-1">
                {handle ? (
                    <p className="truncate font-mono text-sm text-[var(--text-primary)]">{handle}</p>
                ) : null}
                {deepLink ? (
                    <p className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]">
                        {deepLink.replace(/^https?:\/\//, '')}
                    </p>
                ) : null}

                <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button type="button" size="xs" variant="secondary" onClick={() => void copy()}>
                        {copied ? <Check className="mr-1.5 h-3.5 w-3.5 text-[var(--state-success)]" /> : <Copy className="mr-1.5 h-3.5 w-3.5" />}
                        {copied ? 'Copied' : deepLink ? 'Copy link' : 'Copy'}
                    </Button>
                    {deepLink ? (
                        <Button type="button" size="xs" variant="quiet" asChild>
                            <a href={deepLink} target="_blank" rel="noreferrer">
                                Open <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
                            </a>
                        </Button>
                    ) : null}
                </div>
            </div>
        </div>
    );
}
