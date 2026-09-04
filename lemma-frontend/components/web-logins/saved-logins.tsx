'use client';

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Lock, Trash2 } from '@/components/ui/icons';
import {
    useRemoveWebLogin,
    useWebLoginHistory,
    useWebLogins,
} from '@/lib/hooks/use-web-logins';

const relative = (iso: string | null): string => {
    if (!iso) return 'never';
    const seconds = (Date.now() - Date.parse(iso)) / 1000;
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
};

const hostOf = (origin: string): string => {
    try {
        return new URL(origin).host;
    } catch {
        return origin;
    }
};

/**
 * The sites an agent can sign in to on this person's behalf.
 *
 * A credential store nobody can look at is one nobody can trust, so this is
 * deliberately plain: what is saved, when it was last used, and how to take it
 * away. Nothing here can show a secret — the API has no field to return one in.
 */
export function SavedLogins() {
    const { data, isPending, error } = useWebLogins();
    const [showHistory, setShowHistory] = useState(false);
    const history = useWebLoginHistory(showHistory);
    const remove = useRemoveWebLogin();
    const [confirming, setConfirming] = useState<string | null>(null);

    if (isPending) {
        return <p className="text-sm text-[var(--text-tertiary)]">Loading…</p>;
    }
    if (error) {
        return (
            <p className="text-sm text-[var(--text-tertiary)]">
                Saved logins could not be loaded.
            </p>
        );
    }

    const items = data?.items ?? [];

    return (
        <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
                <h2 className="text-sm text-[var(--text-primary)]">Saved logins</h2>
                <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                    Sites you have signed in to in the agent&rsquo;s browser. Lemma keeps the
                    session, never your password.
                </p>
            </div>

            {items.length === 0 ? (
                <p className="text-sm text-[var(--text-tertiary)]">
                    Nothing saved yet. When an agent needs to sign in somewhere, it will ask
                    you once and remember it.
                </p>
            ) : (
                <ul className="flex flex-col divide-y divide-[var(--row-border)] border-y border-[var(--row-border)]">
                    {items.map((login) => (
                        <li
                            key={login.id}
                            className="flex items-center gap-3 py-2.5 text-sm"
                        >
                            <Lock className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                            <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                                {hostOf(login.origin)}
                            </span>
                            {login.has_password ? (
                                <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                                    password saved
                                </span>
                            ) : null}
                            <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                                used {relative(login.last_used_at)}
                            </span>
                            {confirming === login.origin ? (
                                <span className="flex shrink-0 items-center gap-1">
                                    <Button
                                        variant="destructive"
                                        size="xs"
                                        loading={remove.isPending}
                                        onClick={() => {
                                            remove.mutate(login.origin);
                                            setConfirming(null);
                                        }}
                                    >
                                        Forget it
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
                                    aria-label={`Forget ${hostOf(login.origin)}`}
                                    onClick={() => setConfirming(login.origin)}
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
                    Forgetting removes Lemma&rsquo;s copy. It does not sign you out at{' '}
                    {hostOf(confirming)} — do that there if you want the session to stop
                    working.
                </p>
            ) : null}

            <div className="flex flex-col gap-2">
                <Button
                    variant="quiet"
                    size="xs"
                    className="self-start"
                    onClick={() => setShowHistory((open) => !open)}
                >
                    {showHistory ? 'Hide activity' : 'Show activity'}
                </Button>
                {showHistory ? (
                    <ul className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
                        {(history.data?.items ?? []).map((entry, index) => (
                            <li key={`${entry.created_at}-${index}`} className="flex gap-2">
                                <span className="shrink-0 tabular-nums">
                                    {relative(entry.created_at)}
                                </span>
                                <span className="min-w-0 flex-1 truncate">
                                    {entry.action} {hostOf(entry.origin)}
                                    {entry.actor ? ` · ${entry.actor}` : ''}
                                    {entry.outcome === 'ok' ? '' : ` · ${entry.outcome}`}
                                </span>
                            </li>
                        ))}
                        {history.data?.items.length === 0 ? (
                            <li>Nothing yet.</li>
                        ) : null}
                    </ul>
                ) : null}
            </div>
        </div>
    );
}
