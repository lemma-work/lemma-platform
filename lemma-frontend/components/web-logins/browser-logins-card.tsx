'use client';

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { AppWindow, Lock, Trash2 } from '@/components/ui/icons';
import { StepLoader } from '@/components/brand/loader';
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
 * The sites the agent's browser is signed in to, behind one card.
 *
 * A card in "Add your own" rather than a section of its own, because that is
 * what this is: a place your agent can reach that you set up yourself, the
 * same shape as a database or an MCP server. It arrived as a bare heading and
 * a list stapled under a grid of eighty app cards, which read as a footnote
 * to the page rather than a part of it.
 *
 * Nothing is fetched until the dialog opens. Answering costs a round trip
 * into the sandbox, and a door nobody has opened should not be paying for
 * one -- which also means the card cannot show a count, and should not
 * pretend to.
 */
export function BrowserLoginsCard() {
    const [open, setOpen] = useState(false);

    return (
        <>
            {/* Shape copied from `AddYourOwnRow`'s cards deliberately: this
                sits in that grid and any difference would read as a mistake.
                `secondary` with layout-only overrides, no skin. */}
            <Button
                type="button"
                variant="secondary"
                onClick={() => setOpen(true)}
                className="group h-auto w-full justify-start gap-3 rounded-lg p-3 text-left whitespace-normal"
            >
                <span className="connector-monogram connector-monogram-8 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
                    <AppWindow className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-[var(--text-primary)]">
                        Browser logins
                    </span>
                    <span className="block text-xs leading-5 text-[var(--text-tertiary)]">
                        Sites your agent stays signed in to
                    </span>
                </span>
            </Button>

            <BrowserLoginsDialog open={open} onOpenChange={setOpen} />
        </>
    );
}

function BrowserLoginsDialog({
    open,
    onOpenChange,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    // Opening is the ask; waking is a second, louder one. A paused computer
    // says so and offers the button rather than being started by a click that
    // only meant "show me".
    const [wake, setWake] = useState(false);
    const { data, isPending, error } = useWebLogins(wake, open);
    const remove = useRemoveWebLogin();
    const [confirming, setConfirming] = useState<string | null>(null);

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (!next) {
                    // So the next open does not silently start a computer
                    // because of a button pressed some time ago.
                    setWake(false);
                    setConfirming(null);
                }
                onOpenChange(next);
            }}
        >
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Browser logins</DialogTitle>
                    <DialogDescription>
                        Sites you have signed in to in the agent&rsquo;s browser. It keeps
                        the session the way your own browser does, never your password.
                    </DialogDescription>
                </DialogHeader>

                <Body
                    data={data}
                    isPending={isPending}
                    error={error}
                    wake={() => setWake(true)}
                    confirming={confirming}
                    setConfirming={setConfirming}
                    isRemoving={remove.isPending}
                    onRemove={(site) => {
                        remove.mutate(site);
                        setConfirming(null);
                    }}
                />
            </DialogContent>
        </Dialog>
    );
}

function Body({
    data,
    isPending,
    error,
    wake,
    confirming,
    setConfirming,
    isRemoving,
    onRemove,
}: {
    data: { items: { site: string; expires: string | null }[]; sleeping: boolean } | undefined;
    isPending: boolean;
    error: unknown;
    wake: () => void;
    confirming: string | null;
    setConfirming: (site: string | null) => void;
    isRemoving: boolean;
    onRemove: (site: string) => void;
}) {
    if (isPending) {
        return (
            <div className="flex items-center gap-2 py-6 text-sm text-[var(--text-tertiary)]">
                <StepLoader size="xs" />
                Reading the browser&hellip;
            </div>
        );
    }

    if (error) {
        return (
            <p className="py-4 text-sm text-[var(--text-tertiary)]">
                Your browser logins could not be read.
            </p>
        );
    }

    if (data?.sleeping) {
        // "Not running" is "cannot say", not "nothing". The cookies are read
        // over CDP so the browser has to be up to answer, and the profile is
        // on disk either way -- an empty list here would tell somebody their
        // logins were gone.
        return (
            <div className="flex flex-col items-start gap-3 py-2">
                <p className="text-sm text-[var(--text-tertiary)]">
                    The browser is not running, so this cannot be read yet. Whatever it
                    was signed in to is still there.
                </p>
                <Button variant="secondary" size="sm" onClick={wake}>
                    Start it and show me
                </Button>
            </div>
        );
    }

    const items = data?.items ?? [];
    if (items.length === 0) {
        return (
            <p className="py-4 text-sm text-[var(--text-tertiary)]">
                Not signed in to anything. When an agent meets a login wall it will ask
                you once, and the browser will remember after that.
            </p>
        );
    }

    return (
        <div className="flex flex-col gap-3">
            <ul className="flex max-h-80 flex-col gap-1 overflow-y-auto">
                {items.map((login) => (
                    <li
                        key={login.site}
                        className="flex items-center gap-3 rounded-lg border border-[var(--border-subtle)] px-3 py-2.5 text-sm"
                    >
                        <Lock className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                        <span className="min-w-0 flex-1">
                            <span className="block truncate text-[var(--text-primary)]">
                                {login.site}
                            </span>
                            <span className="block text-xs leading-5 text-[var(--text-tertiary)]">
                                {expiryNote(login.expires)}
                            </span>
                        </span>
                        {confirming === login.site ? (
                            <span className="flex shrink-0 items-center gap-1">
                                <Button
                                    variant="destructive"
                                    size="xs"
                                    loading={isRemoving}
                                    onClick={() => onRemove(login.site)}
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
                            // Always visible, not revealed on hover: the
                            // design audit holds `hoverOnlyDisplayReveal` at
                            // zero, and a control you cannot find on a touch
                            // screen is not a control.
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

            {/* No disclaimer any more, and that is the change rather than an
                omission: this used to have to say "forgetting removes Lemma's
                copy, it does not sign you out at the site", because that was
                true of it. The browser holds the session now, so signing out
                signs it out. */}
            {confirming ? (
                <p className="text-xs text-[var(--text-tertiary)]">
                    This signs the agent&rsquo;s browser out of {confirming} and drops its
                    cookies. It does not touch anywhere you are signed in yourself.
                </p>
            ) : null}
        </div>
    );
}
