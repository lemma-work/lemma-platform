'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';

import { buildApiUrl } from '@/components/auth/portal/auth/config';
import {
    buildWhatsAppVerificationMessage,
    WHATSAPP_VERIFICATION_POLL_INTERVAL_MS,
} from '@/lib/identity/whatsapp-mobile-verification';

/**
 * The WhatsApp mobile-verification transaction, without a presentation.
 *
 * Two places need it and they cannot look the same. Profile settings has a page
 * to spend and shows the number, the message and a QR; the surface connect
 * journey is a dialog that already ends on a QR for the agent's own address, and
 * a second one beside it reads as the same code twice. So the state machine —
 * start, poll while the tab is visible, expire on the clock — lives here and
 * each caller draws it.
 */

export type WhatsAppVerificationConfig = {
    available: boolean;
    display_number?: string | null;
};

export type WhatsAppVerificationTransaction = {
    transaction_id: string;
    code: string;
    /** `https://wa.me/<lemma verification number>?text=LEMMA%20VERIFY%20<code>`.
     * The prefilled text is the whole point: the code has to arrive in the
     * message, and nobody retypes ten characters of it correctly. */
    whatsapp_url: string;
    display_number: string;
    expires_at: string;
};

async function responseError(response: Response): Promise<string> {
    try {
        const payload = (await response.json()) as { detail?: string; message?: string };
        return payload.detail || payload.message || 'Mobile verification could not start.';
    } catch {
        return 'Mobile verification could not start.';
    }
}

/**
 * Whether this deployment can verify a number over WhatsApp at all.
 *
 * `auth_whatsapp_mobile_verification_enabled` is off by default and the global
 * number may have no display form, so every caller has to be ready for "no".
 */
export function useWhatsAppVerificationConfig() {
    return useQuery({
        queryKey: ['identity', 'whatsapp-mobile-verification', 'config'],
        queryFn: async (): Promise<WhatsAppVerificationConfig> => {
            try {
                const response = await fetch(
                    buildApiUrl('/auth/mobile-verification/whatsapp/config'),
                    { credentials: 'include' },
                );
                if (!response.ok) return { available: false };
                return (await response.json()) as WhatsAppVerificationConfig;
            } catch {
                return { available: false };
            }
        },
        staleTime: 5 * 60_000,
    });
}

export function useWhatsAppMobileVerification({
    onVerified,
}: {
    onVerified: () => Promise<unknown> | unknown;
}) {
    const [transaction, setTransaction] = useState<WhatsAppVerificationTransaction | null>(null);
    const [starting, setStarting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [now, setNow] = useState(() => Date.now());

    const expiresAt = transaction ? Date.parse(transaction.expires_at) : 0;
    const secondsRemaining = transaction ? Math.max(0, Math.ceil((expiresAt - now) / 1000)) : 0;

    const expire = useCallback(() => {
        setTransaction(null);
        setError('That code expired. Start again for a fresh code.');
    }, []);

    useEffect(() => {
        if (!transaction) return;
        const timer = window.setInterval(() => {
            const current = Date.now();
            setNow(current);
            if (current >= expiresAt) expire();
        }, 1000);
        return () => window.clearInterval(timer);
    }, [expire, expiresAt, transaction]);

    useEffect(() => {
        if (!transaction) return;
        let active = true;
        let poll: number | null = null;

        const check = async () => {
            if (!active || document.visibilityState !== 'visible') return;
            try {
                const response = await fetch(
                    buildApiUrl(
                        `/auth/mobile-verification/whatsapp/status/${encodeURIComponent(transaction.transaction_id)}`,
                    ),
                    { credentials: 'include' },
                );
                if (!response.ok) return;
                const payload = (await response.json()) as {
                    status: 'PENDING' | 'VERIFIED' | 'EXPIRED';
                };
                if (!active) return;
                if (payload.status === 'VERIFIED') {
                    setTransaction(null);
                    await onVerified();
                    toast.success('Mobile number verified');
                } else if (payload.status === 'EXPIRED') {
                    expire();
                }
            } catch {
                // Polling is best effort; the next visible interval retries.
            }
        };
        const beginPolling = () => {
            if (poll !== null) window.clearInterval(poll);
            if (document.visibilityState !== 'visible') return;
            void check();
            poll = window.setInterval(() => void check(), WHATSAPP_VERIFICATION_POLL_INTERVAL_MS);
        };
        const visibilityChanged = () => beginPolling();
        beginPolling();
        document.addEventListener('visibilitychange', visibilityChanged);
        return () => {
            active = false;
            if (poll !== null) window.clearInterval(poll);
            document.removeEventListener('visibilitychange', visibilityChanged);
        };
    }, [expire, onVerified, transaction]);

    const message = useMemo(
        () => (transaction ? buildWhatsAppVerificationMessage(transaction.code) : ''),
        [transaction],
    );

    const copyMessage = useCallback(async () => {
        try {
            await navigator.clipboard.writeText(message);
            toast.success('Full verification message copied');
        } catch {
            toast.error('Could not copy the message. Select it and copy it manually.');
        }
    }, [message]);

    /**
     * Omit the number to bind whichever phone answers.
     *
     * Profile settings has one to declare because the form asked for it; the
     * connect journey does not, and the backend treats an undeclared
     * transaction as "the sender is the answer".
     */
    const start = useCallback(async (mobileNumber?: string) => {
        setStarting(true);
        setError(null);
        try {
            const response = await fetch(
                buildApiUrl('/auth/mobile-verification/whatsapp/start'),
                {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(mobileNumber ? { mobile_number: mobileNumber } : {}),
                },
            );
            if (!response.ok) throw new Error(await responseError(response));
            const created = (await response.json()) as WhatsAppVerificationTransaction;
            setTransaction(created);
            setNow(Date.now());
        } catch (cause) {
            setError(
                cause instanceof Error ? cause.message : 'Mobile verification could not start.',
            );
        } finally {
            setStarting(false);
        }
    }, []);

    const cancel = useCallback(() => setTransaction(null), []);

    return {
        transaction,
        starting,
        error,
        secondsRemaining,
        message,
        start,
        cancel,
        copyMessage,
    };
}
