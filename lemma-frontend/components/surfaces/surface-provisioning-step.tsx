'use client';

import { AlertTriangle, CheckCircle2, ExternalLink, Loader2 } from '@/components/ui/icons';
import QRCode from 'react-qr-code';

import { Button } from '@/components/ui/button';
import type { TelegramManagedBotSetupResponse } from 'lemma-sdk';

/**
 * Waiting while Telegram hands the user a bot of their own.
 *
 * Lemma's manager bot walks them through naming it inside Telegram, then
 * registers the result — so the token never reaches the browser and there is
 * nothing to paste, verify, or mistype. This state's whole job is to keep the
 * hand-off honest: say where they are in it, give them a way back to Telegram
 * on any device, and never claim the surface is live before it is.
 */
export function SurfaceProvisioningStep({
    setup,
    launchUrl,
    hasError,
    onRetry,
}: {
    setup: TelegramManagedBotSetupResponse | undefined;
    /** The manager-bot deep link, held locally so it survives a poll blip. */
    launchUrl: string | null;
    /** The setup itself is unreachable — expired, or the poll is failing. */
    hasError: boolean;
    onRetry: () => void;
}) {
    if (hasError || setup?.status === 'FAILED') {
        return (
            <div className="grid gap-3">
                <p className="surface-verdict is-invalid">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {setup?.error
                        || (hasError
                            ? 'That setup expired before it finished.'
                            : 'Telegram couldn’t finish creating the bot.')}
                </p>
                <Button type="button" variant="outline" size="sm" className="w-fit" onClick={onRetry}>
                    Start again
                </Button>
            </div>
        );
    }

    if (setup?.status === 'COMPLETE') {
        return (
            <p className="surface-verdict is-valid">
                <CheckCircle2 className="h-3.5 w-3.5" />
                {setup.bot_username ? `@${setup.bot_username} is yours` : 'Your bot is ready'}
            </p>
        );
    }

    return (
        <div className="grid gap-3">
            <p className="surface-verdict">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Waiting for you to name it in Telegram…
            </p>

            {launchUrl ? (
                <div className="surface-reach-card">
                    <div className="surface-reach-qr" aria-hidden>
                        <QRCode value={launchUrl} size={96} bgColor="transparent" fgColor="currentColor" />
                    </div>
                    <div className="min-w-0 flex-1">
                        <p className="text-sm leading-6 text-[var(--text-secondary)]">
                            Telegram will ask for a name and a username. Scan this on your phone or
                            open it here — this window updates by itself when you’re done.
                        </p>
                        <Button type="button" size="xs" variant="outline" className="mt-2" asChild>
                            <a href={launchUrl} target="_blank" rel="noreferrer">
                                Open Telegram <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
                            </a>
                        </Button>
                    </div>
                </div>
            ) : null}
        </div>
    );
}
