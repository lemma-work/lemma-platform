'use client';

import Link from 'next/link';
import { useEffect, useRef } from 'react';

import { StepLoader } from '@/components/brand/loader';
import { Button } from '@/components/ui/button';
import { Clock, Copy, ExternalLink, Smartphone } from '@/components/ui/icons';
import { useProfile } from '@/lib/hooks/use-user';
import {
    useWhatsAppMobileVerification,
    useWhatsAppVerificationConfig,
} from '@/lib/identity/use-whatsapp-mobile-verification';
import { getSurfacePlatformKey } from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

/**
 * Whether the person who just connected this will be recognized when they use it.
 *
 * On WhatsApp there is no account to link and no handle to match: the only thing
 * an inbound message carries is the sender's number, so Lemma resolves it
 * against the mobile number on a profile. A member whose profile has none is a
 * stranger to their own agent — the connect journey ends on a QR, they scan it,
 * and the reply is a sign-up link. That is the same journey working exactly as
 * built, which is why it has to be said here rather than found out there.
 *
 * A number already on the profile is enough, verified or not: identity
 * resolution takes a single unverified match. So this asks nothing of the people
 * who have one — and for everyone else it never asks for the number either. The
 * code is minted on arrival and whichever phone sends it is the phone that gets
 * bound. Typing a number to prove a number is friction Telegram's OIDC
 * verification never had, and WhatsApp holds the same proof in Meta's signature.
 *
 * Only WhatsApp. Telegram resolves the same way but asks for a contact share in
 * the chat when it can't (`unresolved_sender_reply`), so it repairs itself;
 * Slack, Teams and mailboxes carry an identity of their own.
 */
export function SurfaceMobileIdentity({ surface }: { surface: AssistantSurface }) {
    if (getSurfacePlatformKey(surface) !== 'WHATSAPP') return null;
    return <WhatsAppSenderIdentity />;
}

function WhatsAppSenderIdentity() {
    const { data: profile, isLoading, refetch } = useProfile();
    const { data: config } = useWhatsAppVerificationConfig();
    const { transaction, starting, error, secondsRemaining, message, start, copyMessage } =
        useWhatsAppMobileVerification({ onVerified: refetch });

    const known = Boolean(profile?.mobile_number);
    const canVerify = config?.available === true;
    // One mint per mount, and never a retry loop: a failed start leaves an error
    // on screen with a button, because an effect that reran on its own failure
    // would spend the whole start rate limit in a second.
    const minted = useRef(false);
    useEffect(() => {
        if (minted.current || !profile || known || !canVerify) return;
        minted.current = true;
        void start();
    }, [canVerify, known, profile, start]);

    if (isLoading || !profile || known) return null;

    // Nothing to mint where the deployment has verification switched off, and
    // the profile is then the only place to put a number.
    if (!canVerify) {
        return (
            <div className="surface-panel-muted grid gap-2 p-3">
                <p className="text-sm font-medium text-[var(--text-primary)]">
                    WhatsApp won’t know it’s you
                </p>
                <p className="text-xs leading-5 text-[var(--text-secondary)]">
                    Lemma matches an incoming WhatsApp message to your account by mobile
                    number, and your profile doesn’t have one. Add it and this number
                    answers you instead of sending a sign-up link.
                </p>
                <Button type="button" size="xs" variant="secondary" asChild className="w-fit">
                    <Link href="/profile">
                        <Smartphone className="mr-1.5 h-3.5 w-3.5" />
                        Add your mobile number
                    </Link>
                </Button>
            </div>
        );
    }

    if (!transaction) {
        return (
            <div className="surface-panel-muted grid gap-2 p-3">
                <p className="text-sm font-medium text-[var(--text-primary)]">
                    WhatsApp won’t know it’s you yet
                </p>
                {error ? (
                    <>
                        <p className="text-xs text-[var(--state-error)]" role="alert">
                            {error}
                        </p>
                        <Button
                            type="button"
                            size="xs"
                            variant="secondary"
                            className="w-fit"
                            loading={starting}
                            loadingLabel="Preparing WhatsApp"
                            onClick={() => void start()}
                        >
                            Try again
                        </Button>
                    </>
                ) : (
                    <p className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                        <StepLoader size="xs" />
                        Preparing a verification message…
                    </p>
                )}
            </div>
        );
    }

    return (
        <div className="surface-panel-muted grid gap-2 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="text-sm font-medium text-[var(--text-primary)]">
                    Send this once, from your phone
                </p>
                {/* No live region around this: it reticks every second, and a
                    polite region wrapping a clock announces the whole panel
                    sixty times a minute. */}
                <span className="chip chip-sm chip-pill chip-muted">
                    <Clock className="h-3 w-3" />
                    {Math.floor(secondsRemaining / 60)}:
                    {String(secondsRemaining % 60).padStart(2, '0')}
                </span>
            </div>
            <code className="select-all break-all font-mono text-sm text-[var(--text-primary)]">
                {message}
            </code>
            {/* The verification number is Lemma's own, and the QR right above
                this is the agent's. Two WhatsApp numbers a few pixels apart is
                exactly the confusion worth spending a line on. */}
            <p className="text-xs leading-5 text-[var(--text-secondary)]">
                It goes to {transaction.display_number} — Lemma’s verification number, not
                this agent. Whichever phone sends it becomes the number this agent knows
                you by.
            </p>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
                <Button type="button" size="xs" variant="primary" asChild>
                    <a href={transaction.whatsapp_url} target="_blank" rel="noreferrer">
                        Open WhatsApp
                        <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
                    </a>
                </Button>
                <Button type="button" size="xs" variant="secondary" onClick={() => void copyMessage()}>
                    <Copy className="mr-1.5 h-3.5 w-3.5" />
                    Copy message
                </Button>
            </div>
        </div>
    );
}
