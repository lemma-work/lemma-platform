'use client';

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Lock, Trash2 } from '@/components/ui/icons';
import { useRemoveWebLogin, useWebLogins } from '@/lib/hooks/use-web-logins';

const expiryNote = (iso: string | null): string => {
    if (!iso) return 'until the browser restarts';
    const days = (Date.parse(iso) - Date.now()) / 86_400_000;
    if (days < 0) return 'expired';
    if (days < 1) return 'expires today';
    if (days < 2) return 'expires tomorrow';
    if (days < 60) return `expires in ${Math.round(days)} days`;
    return `expires in ${Math.round(days / 30)} months`;
};

/**
 * The sites the agent's browser is signed in to.
 *
 * Read from the browser itself rather than from a store Lemma keeps, which
 * changes what this screen can honestly say. It used to have to disclaim
 * itself -- "forgetting removes Lemma's copy, it does not sign you out" --
 * because that was true. Forgetting signs the browser out now, so the
 * disclaimer is gone and the button means what it says.
 *
 * Nothing here can show a secret, and that is no longer a promise about the
 * response shape: cookie values never leave the sandbox at all.
 */
export function SavedLogins() {
    // Starts without waking anything. Asking the browser means a round trip
    // into the sandbox, and opening a settings page is not a reason to start
    // somebody's computer.
    const [wake, setWake] = useState(false);
    const { data, isPending, error } = useWebLogins(wake);
    const remove = useRemoveWebLogin();
    const [confirming, setConfirming] = useState<string | null>(null);

    const heading = (
        <div className="flex flex-col gap-1">
            <h2 className="text-sm text-[var(--text-primary)]">Browser logins</h2>
            <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                Sites you have signed in to in the agent&rsquo;s browser. It keeps the
                session the way your own browser does, never your password.
            </p>
        </div>
    );

    if (isPending) {
        return (
            <div className="flex flex-col gap-4">
                {heading}
                <p className="text-sm text-[var(--text-tertiary)]">Loading…</p>
            </div>
        );
    }
    if (error) {
        return (
            <div className="flex flex-col gap-4">
                {heading}
                <p className="text-sm text-[var(--text-tertiary)]">
                    Your browser logins could not be read.
                </p>
            </div>
        );
    }

    if (data?.sleeping) {
        return (
            <div className="flex flex-col items-start gap-3">
                {heading}
                <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                    The browser is not running, so this cannot be read yet. Whatever it
                    was signed in to is still there — the list is read from the browser
                    itself, which has to be up to answer.
                </p>
                <Button variant="secondary" size="xs" onClick={() => setWake(true)}>
                    Start it and show me
                </Button>
            </div>
        );
    }

    const items = data?.items ?? [];

    return (
        <div className="flex flex-col gap-4">
            {heading}

            {items.length === 0 ? (
                <p className="text-sm text-[var(--text-tertiary)]">
                    Not signed in to anything. When an agent meets a login wall it will
                    ask you once, and the browser will remember after that.
                </p>
            ) : (
                <ul className="flex flex-col divide-y divide-[var(--row-border)] border-y border-[var(--row-border)]">
                    {items.map((login) => (
                        <li
                            key={login.site}
                            className="flex items-center gap-3 py-2.5 text-sm"
                        >
                            <Lock className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                            <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                                {login.site}
                            </span>
                            <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                                {expiryNote(login.expires)}
                            </span>
                            {confirming === login.site ? (
                                <span className="flex shrink-0 items-center gap-1">
                                    <Button
                                        variant="destructive"
                                        size="xs"
                                        loading={remove.isPending}
                                        onClick={() => {
                                            remove.mutate(login.site);
                                            setConfirming(null);
                                        }}
                                    >
                                        Sign out
                                    </Button>
                                    <Button
                                        variant="quiet"
                                        size="xs"
                                        onClick={() => setConfirming(null)}
                                    >
                                        Keep
                                    </Button>
                                </span>
                            ) : (
                                <Button
                                    variant="quiet"
                                    size="xs"
                                    aria-label={`Sign out of ${login.site}`}
                                    onClick={() => setConfirming(login.site)}
                                >
                                    <Trash2 className="size-3.5" />
                                </Button>
                            )}
                        </li>
                    ))}
                </ul>
            )}

            {confirming ? (
                <p className="max-w-prose text-xs text-[var(--text-tertiary)]">
                    This signs the agent&rsquo;s browser out of {confirming} and drops its
                    cookies. It does not touch anywhere you are signed in yourself.
                </p>
            ) : null}
        </div>
    );
}
