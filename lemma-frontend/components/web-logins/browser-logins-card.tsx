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
            {/* Wider than the default `max-w-lg`, and scrolled in the body
                rather than the shell, following `function-access-dialog`:
                a real browser accumulates a domain per site visited, not per
                site signed in to, so this list is long more often than it is
                short -- and the header has to stay put while it is read. */}
            <DialogContent className="max-w-2xl gap-0 overflow-hidden p-0">
                <DialogHeader className="border-b border-[color:var(--border-subtle)] px-5 py-4 pr-12 text-left">
                    <DialogTitle>Browser logins</DialogTitle>
                    <DialogDescription className="text-xs">
                        Sites the agent&rsquo;s browser holds cookies for. It keeps a
                        session the way your own browser does, never your password.
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

type Site = { site: string; expires: string | null; signed_in: boolean };

/** A band between the two groups. Sticky, so it still names what you are
 *  looking at once the list below it has been scrolled into view. */
function Heading({ children }: { children: React.ReactNode }) {
    return (
        <p className="sticky top-0 z-10 bg-[var(--surface-1)] px-5 pt-3 pb-1.5 text-xs text-[var(--text-secondary)]">
            {children}
        </p>
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
    data: { items: Site[]; sleeping: boolean } | undefined;
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
            <div className="flex items-center gap-2 px-5 py-6 text-sm text-[var(--text-tertiary)]">
                <StepLoader size="xs" />
                Reading the browser&hellip;
            </div>
        );
    }

    if (error) {
        return (
            <p className="px-5 py-6 text-sm text-[var(--text-tertiary)]">
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
            <div className="flex flex-col items-start gap-3 px-5 py-5">
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
            <p className="px-5 py-6 text-sm text-[var(--text-tertiary)]">
                Nothing here yet. When an agent meets a login wall it will ask you
                once, and the browser will remember after that.
            </p>
        );
    }

    // Only split when the split says something. Marks begin empty on every
    // profile that predates them, and a dialog that hid four sites behind a
    // collapsed "other" because nobody had answered a sign-in yet would be
    // worse than the flat list it replaced.
    const confirmed = items.filter((item) => item.signed_in);
    const rest = items.filter((item) => !item.signed_in);
    const split = confirmed.length > 0 && rest.length > 0;

    const row = (login: Site) => (
        <li
            key={login.site}
            className="flex items-center gap-3 px-5 py-2.5 text-sm"
        >
            <Lock className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
            <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">
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
                        loading={isRemoving}
                        onClick={() => onRemove(login.site)}
                    >
                        Clear cookies
                    </Button>
                    <Button variant="quiet" size="xs" onClick={() => setConfirming(null)}>
                        Keep
                    </Button>
                </span>
            ) : (
                // Always visible, not revealed on hover: the design audit
                // holds `hoverOnlyDisplayReveal` at zero, and a control you
                // cannot find on a touch screen is not a control.
                <Button
                    variant="quiet"
                    size="xs"
                    aria-label={`Clear cookies for ${login.site}`}
                    onClick={() => setConfirming(login.site)}
                >
                    <Trash2 className="size-3.5" />
                </Button>
            )}
        </li>
    );

    return (
        <>
            {/* One line per site, name left and expiry right, rather than the
                two-line block this started as: stacked, every row wasted the
                width it had been given and half as many fit before scrolling.
                Dividers rather than a border each -- at twenty rows, twenty
                outlines read as a stack of cards instead of a list.

                The scroll is on this alone, so the header above and the note
                below stay put. A confirmation that scrolled away from the row
                it belongs to is worse than none. */}
            <div className="max-h-[min(70dvh,32rem)] overflow-y-auto">
                {split ? (
                    <Heading>Signed in</Heading>
                ) : null}
                <ul className="flex flex-col divide-y divide-[var(--row-border)]">
                    {(split ? confirmed : items).map(row)}
                </ul>

                {split ? (
                    <>
                        {/* Everything else the profile picked up. A browser
                            collects a cookie domain per site *visited*, so
                            this is where the ad networks and the video you
                            watched once end up -- worth being able to clear,
                            not worth reading first. */}
                        <Heading>
                            Other sites with cookies
                            <span className="ml-1.5 text-[var(--text-tertiary)]">
                                {rest.length}
                            </span>
                        </Heading>
                        <ul className="flex flex-col divide-y divide-[var(--row-border)]">
                            {rest.map(row)}
                        </ul>
                    </>
                ) : null}
            </div>

            {/* No disclaimer any more, and that is the change rather than an
                omission: this used to have to say "forgetting removes Lemma's
                copy, it does not sign you out at the site", because that was
                true of it. The browser holds the session now, so signing out
                signs it out. */}
            {confirming ? (
                <p className="border-t border-[color:var(--border-subtle)] px-5 py-3 text-xs text-[var(--text-tertiary)]">
                    This drops {confirming}&rsquo;s cookies from the agent&rsquo;s
                    browser, which signs it out of most sites. A site that keeps its
                    token elsewhere may stay signed in. It does not touch anywhere you
                    are signed in yourself.
                </p>
            ) : null}
        </>
    );
}
