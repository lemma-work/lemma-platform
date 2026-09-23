"use client";

import { useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, GlobeIcon, LockIcon } from "@/ui/icons";
import { useForgetWebLogin, useWebLogins, type WebLogin } from "./queries";
import { groupSites, loginNote, saidSoFor } from "./logins";

/** The sites your teammate's browser is signed in to.
 *
 *  It belongs on this view rather than in settings because it is a fact about
 *  this machine: the browser on the screen above is the browser these logins
 *  are in, and they survive a suspend because its profile lives in the durable
 *  home. That is the whole reason a sign-in is worth doing once.
 *
 *  Shut until somebody opens it, and nothing is asked for until then. Reading
 *  this costs a round trip into the sandbox — it is read from Chrome, not from
 *  a table — so rendering the door must not be what pays for the walk through
 *  it. `wake` is off for the same reason one step further out: opening a panel
 *  should not be what starts somebody's computer.
 */
export function Logins({ visible }: { visible: boolean }) {
    const [open, setOpen] = useState(false);
    const [wake, setWake] = useState(false);
    const [forgetting, setForgetting] = useState<string | null>(null);
    const logins = useWebLogins(wake, visible && open);
    const forget = useForgetWebLogin();

    const items = logins.data?.items ?? [];
    const sleeping = logins.data?.sleeping === true;
    /* What the browser holds, told apart from what somebody signed in to. See
       `groupSites`: a profile collects a cookie domain per site *visited*, and
       this door's own label was claiming every one of them as a login. */
    const sites = groupSites(items);

    return (
        <section className="logins">
            <button className="logins__door" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? <ChevronDownIcon size={13} /> : <ChevronRightIcon size={13} />}
                <span>Sites this browser is signed in to</span>
                {/* What the heading under it covers, which is the whole
                    list when there is one heading and the sign-ins when there
                    are two. Counting every cookie domain here was the same
                    overclaim as the label. */}
                {open && !sleeping && !logins.isPending && (
                    <small>{sites.split ? sites.signedIn.length : items.length}</small>
                )}
            </button>

            {open && (
                <div className="logins__body">
                    {logins.isPending && <p className="computer-note" role="status">Asking the browser…</p>}
                    {logins.isError && (
                        <p className="computer-note" role="alert">
                            The browser could not be asked just now.{" "}
                            <button className="computer-inline" onClick={() => void logins.refetch()}>Try again</button>
                        </p>
                    )}

                    {sleeping && (
                        <p className="computer-note">
                            This computer is asleep, and its browser is the only thing that knows.{" "}
                            <button className="computer-inline" onClick={() => setWake(true)}>Wake it and ask</button>
                        </p>
                    )}

                    {!sleeping && !logins.isPending && items.length === 0 && (
                        <p className="computer-note">
                            Nothing yet. A teammate asks you to sign in when a site it needs wants a person,
                            and the browser keeps that session afterwards.
                        </p>
                    )}

                    {!sleeping && (sites.split ? sites.signedIn : items).map((login) => (
                        <Row
                            key={login.site}
                            login={login}
                            forgetting={forgetting === login.site}
                            busy={forget.isPending && forget.variables === login.site}
                            onAsk={() => setForgetting(login.site)}
                            onKeep={() => setForgetting(null)}
                            onForget={() => forget.mutate(login.site, { onSettled: () => setForgetting(null) })}
                        />
                    ))}

                    {/* Everything else the profile picked up on the way past:
                        the ad networks, the font host, the video watched once.
                        Worth being able to clear, not worth reading first —
                        and not worth calling a login, which is what the one
                        list above it was doing. */}
                    {!sleeping && sites.split && (
                        <>
                            <p className="logins__band">
                                Other sites with cookies <small>{sites.other.length}</small>
                            </p>
                            {sites.other.map((login) => (
                                <Row
                                    key={login.site}
                                    login={login}
                                    forgetting={forgetting === login.site}
                                    busy={forget.isPending && forget.variables === login.site}
                                    onAsk={() => setForgetting(login.site)}
                                    onKeep={() => setForgetting(null)}
                                    onForget={() => forget.mutate(login.site, { onSettled: () => setForgetting(null) })}
                                />
                            ))}
                        </>
                    )}

                    {forget.isError && (
                        <p className="computer-note" role="alert">
                            That did not sign out. It needs the computer running — it really signs the browser
                            out rather than forgetting a copy, so there is nothing to do while it is stopped.
                        </p>
                    )}
                </div>
            )}
        </section>
    );
}

function Row({ login, forgetting, busy, onAsk, onKeep, onForget }: {
    login: WebLogin;
    forgetting: boolean;
    busy: boolean;
    onAsk: () => void;
    onKeep: () => void;
    onForget: () => void;
}) {
    if (forgetting) {
        /* Named, and with what it means said out loud, rather than "are you
           sure?" — which is a question nobody has ever read. A teammate
           mid-run on this site is the case worth warning about. */
        return (
            <div className="logins__row logins__row--asking" role="alertdialog" aria-label={"Sign out of " + login.site}>
                <p>
                    Sign the browser out of <strong>{login.site}</strong>? A teammate working there will be
                    asked for you again.
                </p>
                <div className="logins__actions">
                    <button className="btn btn--danger" disabled={busy} onClick={onForget}>
                        {busy ? "Signing out…" : "Sign out"}
                    </button>
                    <button className="btn" disabled={busy} onClick={onKeep}>Keep it</button>
                </div>
            </div>
        );
    }

    return (
        <div className="logins__row">
            <span className="logins__mark">{saidSoFor(login) ? <LockIcon size={16} /> : <GlobeIcon size={16} />}</span>
            <span className="logins__what">
                <strong>{login.site}</strong>
                <small>{loginNote(login, new Date())}</small>
            </span>
            <button className="computer-inline" onClick={onAsk}>Sign out</button>
        </div>
    );
}
