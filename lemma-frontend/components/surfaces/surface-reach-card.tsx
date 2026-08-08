'use client';

import { useState } from 'react';
import QRCode from 'react-qr-code';
import { Check, Copy, ExternalLink } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { getSurfaceDeepLink, getSurfaceIdentity, getSurfacePlatformKey } from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

/**
 * How a human reaches this surface — the proof that setup worked.
 *
 * Every connect journey ends here rather than on a toast, because the handle and
 * a link to it are the only things that let someone confirm the agent is
 * actually reachable. Platforms with no direct-open convention (Slack, Teams,
 * mailboxes) get the handle without the QR.
 */
/**
 * Platforms whose handle is a *name*, not an address.
 *
 * Slack and Teams resolve to the bot's display name. There is nothing to do
 * with it: you reach the bot by typing `@` and letting the client autocomplete
 * from its own directory, never by pasting a string from here. Rendered anyway
 * it was one bare word above a Copy button that copied nothing useful — so the
 * card sits this one out, and the states that showed it say something real
 * instead (what happens next in Slack, or the routing below).
 */
const NAME_NOT_ADDRESS = new Set(['SLACK', 'TEAMS']);

/** How to use the handle, where there is no link or QR to make it obvious. */
function reachCaption(surface: AssistantSurface): string | null {
    switch (getSurfacePlatformKey(surface)) {
        case 'GMAIL':
        case 'OUTLOOK':
        case 'RESEND':
            return 'Mail sent here becomes work:';
        default:
            return null;
    }
}

/** Whether the card will render anything — callers that would otherwise show an
 * empty state need to know before laying out around it. */
export function hasReachCard(surface: AssistantSurface): boolean {
    if (NAME_NOT_ADDRESS.has(getSurfacePlatformKey(surface))) return false;
    const handle = surface.reach?.handle || getSurfaceIdentity(surface) || surface.reach?.email;
    return Boolean(handle || getSurfaceDeepLink(surface));
}

export function SurfaceReachCard({ surface }: { surface: AssistantSurface }) {
    const [copied, setCopied] = useState(false);
    const handle = surface.reach?.handle || getSurfaceIdentity(surface) || surface.reach?.email || null;
    const deepLink = getSurfaceDeepLink(surface);
    const caption = deepLink ? null : reachCaption(surface);

    if (!hasReachCard(surface)) return null;

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
                {/* A handle on its own is a mystery string. With a QR and a link
                    beside it the shape gives it away, but Slack, Teams and email
                    have neither — there it rendered as one bare word above a
                    Copy button, and nothing said what it was for. */}
                {caption ? (
                    <p className="text-xs text-[var(--text-secondary)]">{caption}</p>
                ) : null}
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
