'use client';

import { useState } from 'react';
import { Check, Copy, Mail } from '@/components/ui/icons';
import { toast } from 'sonner';

import { splitEmail } from '@/lib/surfaces/agent-email';
import { cn } from '@/lib/utils';

/**
 * An agent's own email address, rendered as the address it is.
 *
 * Every agent gets one the moment it is created, and until now the platform's
 * only way of saying so was a chip in the "Reached by" row that truncated it —
 * `roundtable@ops.lemm…`, an unreadable string beside an empty icon box. An
 * address is not a status: it is a thing a person copies and gives to someone
 * else, so the two affordances it needs are legibility and copy.
 *
 * The local part carries the weight and the domain goes quiet. Every managed
 * address in a deployment shares one domain, so drawing both at the same weight
 * spends the line on the half that identifies nothing — and, in a chip, is
 * exactly which half gets truncated away.
 */
export function AgentEmail({
    address,
    size = 'md',
    /**
     * The agent doesn't exist yet, so this address doesn't either — drop the
     * copy button. Offering to copy a string that nothing will deliver to is
     * how someone ends up pasting it into a mail they then send.
     *
     * That it *is* a preview is said by the sentence around it, not by a badge:
     * "People will be able to email it at …" already carries the tense, and a
     * "will be" chip on the end of that read as a fragment.
     */
    preview = false,
    showCopy = true,
    className,
}: {
    address: string | null | undefined;
    size?: 'sm' | 'md';
    preview?: boolean;
    showCopy?: boolean;
    className?: string;
}) {
    const [copied, setCopied] = useState(false);
    const parts = splitEmail(address);
    if (!parts) return null;

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(`${parts.local}${parts.domain}`);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Could not copy the address');
        }
    };

    return (
        <span className={cn('agent-email', size === 'sm' && 'agent-email-sm', className)}>
            <Mail className="agent-email-icon" aria-hidden />
            {/* One element, not two, so a screen reader and a text selection both
                get the address whole — the split is presentational. */}
            <span className="agent-email-address" title={`${parts.local}${parts.domain}`}>
                <span className="agent-email-local">{parts.local}</span>
                <span className="agent-email-domain">{parts.domain}</span>
            </span>
            {showCopy && !preview ? (
                <button
                    type="button"
                    onClick={(event) => {
                        // Lives inside cards and rows that navigate. Copying an
                        // address should never also take you somewhere.
                        event.preventDefault();
                        event.stopPropagation();
                        void copy();
                    }}
                    className="agent-email-copy custom-focus-ring"
                    aria-label={copied ? 'Address copied' : `Copy ${parts.local}${parts.domain}`}
                >
                    {copied ? <Check className="h-3.5 w-3.5 text-[var(--state-success)]" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
                </button>
            ) : null}
        </span>
    );
}
