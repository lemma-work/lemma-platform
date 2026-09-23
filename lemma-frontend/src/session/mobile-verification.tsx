"use client";

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import QRCode from "react-qr-code";
import type { UserResponse } from "lemma-sdk";
import { source } from "@/data";
import { CheckIcon, ClockIcon, CopyIcon, ExternalIcon, RefreshIcon, WarningIcon } from "@/ui/icons";
import { lemma } from "./client";
import {
    useTelegramMobileVerification,
    useTelegramVerificationConfig,
    useWhatsAppMobileVerification,
    useWhatsAppVerificationConfig,
    type CopyState,
    type WhatsAppTransaction,
} from "./use-mobile-verification";

/** Two drawings of one transaction.
 *
 *  `VerifyMobile` is the account-settings treatment: a panel wide enough for
 *  the number, the message and a QR, because that is where somebody went
 *  deliberately to sort their number out. `KnownSender` is the strip under a
 *  WhatsApp address in the reach sheet, where the point is a sentence and a
 *  button and the QR a few pixels above belongs to a different number.
 */

function Countdown({ seconds }: { seconds: number }) {
    return (
        /* No live region. It reticks every second, and a polite region around
           a clock reads the panel out sixty times a minute. */
        <span className="verify__clock">
            <ClockIcon size={14} />
            {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
        </span>
    );
}

/** The code, as something a phone camera can eat.
 *
 *  Fixed ink on fixed paper, in both themes. Every other surface in this app
 *  follows the theme; this one cannot, because in dark mode the same tokens
 *  invert into a light-on-dark QR and a good half of the scanners in the world
 *  quietly refuse those. The two values are the light palette's own `--paper`
 *  and `--ink`, so it is still this app's white and this app's black — just
 *  not the ones this screen happens to be using. */
function Code({ url }: { url: string }) {
    return (
        <div className="verify__qr">
            <div className="verify__qr-paper">
                <QRCode
                    value={url}
                    size={116}
                    level="M"
                    bgColor="#fffefa"
                    fgColor="#20211f"
                    title="Scan to open the verification message in WhatsApp"
                />
            </div>
            <small>Scan with the phone</small>
        </div>
    );
}

function CopyMessage({ state, onCopy }: { state: CopyState; onCopy: () => void }) {
    const icon = state === "done" ? CheckIcon : state === "failed" ? WarningIcon : CopyIcon;
    const Icon = icon;
    return (
        <button className="btn verify__copy" type="button" data-state={state} onClick={onCopy}>
            <Icon size={14} />
            {state === "done" ? "Copied" : state === "failed" ? "Could not copy" : "Copy the message"}
            <span className="sr-only" role="status">
                {state === "done" ? "Message copied"
                    : state === "failed" ? "Could not copy. Select the message above and copy it."
                    : ""}
            </span>
        </button>
    );
}

function OpenWhatsApp({ url }: { url: string }) {
    return (
        <a className="btn btn--primary" href={url} target="_blank" rel="noreferrer">
            Open WhatsApp <ExternalIcon size={13} />
        </a>
    );
}

/* ------------------------------------------------------------------ */
/* Account settings                                                    */
/* ------------------------------------------------------------------ */

function Transaction({
    transaction,
    message,
    copied,
    seconds,
    onCopy,
    onCancel,
}: {
    transaction: WhatsAppTransaction;
    message: string;
    copied: CopyState;
    seconds: number;
    onCopy: () => void;
    onCancel: () => void;
}) {
    return (
        <section className="verify">
            <header className="verify__head">
                <img className="channel-icon" src="/connector-logos/whatsapp.svg" width={20} height={20} alt="" aria-hidden="true" />
                <div>
                    <b>Send this message from the phone you are claiming</b>
                    <small>Whichever number sends it becomes the number on your profile.</small>
                </div>
                <Countdown seconds={seconds} />
            </header>

            <div className="verify__grid">
                <div className="verify__lines">
                    <div className="verify__line">
                        <span className="verify__label">Send to</span>
                        <a className="verify__number" href={transaction.whatsapp_url} target="_blank" rel="noreferrer">
                            {transaction.display_number}
                        </a>
                        {/* Said plainly because the reach sheet puts a
                            teammate's own WhatsApp number a few rows away, and
                            two WhatsApp numbers that look alike is exactly the
                            confusion worth spending a line on. */}
                        <small>Lemma&rsquo;s verification number, not a teammate&rsquo;s.</small>
                    </div>
                    <div className="verify__line">
                        <span className="verify__label">Message to send</span>
                        <code className="verify__code">{message}</code>
                        <CopyMessage state={copied} onCopy={onCopy} />
                    </div>
                </div>
                <Code url={transaction.whatsapp_url} />
            </div>

            <div className="verify__acts">
                <OpenWhatsApp url={transaction.whatsapp_url} />
                <button className="linkish verify__quiet" type="button" onClick={onCancel}>Cancel</button>
            </div>
        </section>
    );
}

/** Proving the number in the field above is yours.
 *
 *  Nothing has to be saved first: on success the server writes the number and
 *  the verified stamp itself, from whichever phone answered. That is why
 *  `onVerified` hands back the whole user — the field has to be told what it
 *  now holds, or the form would keep showing what was typed and save it back
 *  over a number that has just been proved. */
export function VerifyMobile({
    number,
    complete,
    onVerified,
}: {
    number: string;
    complete: boolean;
    onVerified: (user: UserResponse) => void;
}) {
    const whatsapp = useWhatsAppVerificationConfig();
    const telegram = useTelegramVerificationConfig();
    const wa = useWhatsAppMobileVerification({ onVerified });
    const tg = useTelegramMobileVerification({ onVerified });

    const canWhatsApp = whatsapp.data?.available === true;
    const canTelegram = telegram.data?.enabled === true && telegram.data.sameSite;
    if (!canWhatsApp && !canTelegram) return null;

    if (wa.transaction) {
        return (
            <Transaction
                transaction={wa.transaction}
                message={wa.message}
                copied={wa.copied}
                seconds={wa.secondsRemaining}
                onCopy={wa.copy}
                onCancel={wa.cancel}
            />
        );
    }

    if (tg.waiting) {
        return (
            <div className="verify verify--waiting guided">
                <span className="guided__wait"><RefreshIcon size={13} /> Waiting for Telegram…</span>
                <p>
                    Finish in the tab that just opened. Telegram hands you back to Lemma&rsquo;s web app
                    rather than here, so close that tab afterwards — this page notices on its own.
                </p>
                <button className="linkish verify__quiet" type="button" onClick={tg.cancel}>Stop waiting</button>
            </div>
        );
    }

    const failed = wa.error ?? tg.error;

    return (
        <div className="verify__start">
            {canWhatsApp && (
                <button
                    className="btn"
                    type="button"
                    /* A number with no country code is not something to spend
                       one of an hour's few attempts on. */
                    disabled={!complete || wa.starting}
                    onClick={() => void wa.start(number)}
                >
                    <img className="channel-icon" src="/connector-logos/whatsapp.svg" width={15} height={15} alt="" aria-hidden="true" />
                    {wa.starting ? "Preparing…" : "Verify over WhatsApp"}
                </button>
            )}
            {canTelegram && (
                <button className="btn" type="button" onClick={tg.start}>
                    <img className="channel-icon" src="/connector-logos/telegram.svg" width={15} height={15} alt="" aria-hidden="true" />
                    Verify over Telegram
                </button>
            )}
            {/* Telegram never asks for the number — it reads it from the
                account that answers — so this is only about the WhatsApp
                button, and only worth saying when that is the one on offer. */}
            {canWhatsApp && !complete && (
                <span className="verify__hint">
                    {number ? "Add the country code before verifying." : "Enter a mobile number first."}
                </span>
            )}
            {failed && <span className="profile-form__problem" role="alert">{failed}</span>}
        </div>
    );
}

/* ------------------------------------------------------------------ */
/* The reach sheet                                                     */
/* ------------------------------------------------------------------ */

/** Whether the person who just connected this will be recognised when they use it.
 *
 *  On WhatsApp there is no account to link and no handle to match: an inbound
 *  message carries the sender's number and nothing else, so Lemma resolves it
 *  against the mobile number on a profile. A member whose profile has none is
 *  a stranger to their own teammate — they message the number they were just
 *  given and get a sign-up link back. That is the journey working exactly as
 *  built, which is why it has to be said here rather than found out there.
 *
 *  A number already on the profile is enough, verified or not: resolution
 *  takes a single unverified match. So this asks nothing of the people who
 *  have one, and never asks the rest to type one either — the code is minted
 *  on arrival and whichever phone sends it is the phone that gets bound.
 *
 *  Only WhatsApp. Telegram resolves the same way but asks for a contact share
 *  in the chat when it cannot, so it repairs itself; Slack, Teams and
 *  mailboxes carry an identity of their own. */
export function KnownSender() {
    const live = source.label !== "sample";
    const user = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: live,
        staleTime: 5 * 60_000,
        gcTime: 30 * 60_000,
    });
    const config = useWhatsAppVerificationConfig();
    const wa = useWhatsAppMobileVerification({ onVerified: () => undefined });

    const known = Boolean(user.data?.mobile_number);
    const canVerify = config.data?.available === true;

    /* One mint per mount, and never a retry loop: a failed start leaves an
       error and a button, because an effect that reran on its own failure
       would spend the whole hour's rate limit in a second. */
    const minted = useRef(false);
    const start = wa.start;
    useEffect(() => {
        if (minted.current || !user.data || known || !canVerify) return;
        minted.current = true;
        void start();
    }, [canVerify, known, start, user.data]);

    if (!live || !user.data || known) return null;

    if (!canVerify) {
        return (
            <div className="verify__strip">
                <b>WhatsApp will not know it is you</b>
                <p>
                    Lemma matches an incoming WhatsApp message to your account by mobile number,
                    and your profile does not have one. Add it in Settings and this number answers
                    you instead of sending a sign-up link.
                </p>
            </div>
        );
    }

    if (!wa.transaction) {
        return (
            <div className="verify__strip">
                <b>WhatsApp will not know it is you yet</b>
                {wa.error ? (
                    <>
                        <p role="alert" className="verify__strip-error">{wa.error}</p>
                        <button className="btn" type="button" disabled={wa.starting} onClick={() => void wa.start()}>
                            {wa.starting ? "Preparing…" : "Try again"}
                        </button>
                    </>
                ) : (
                    <span className="guided__wait"><RefreshIcon size={13} /> Preparing a verification message…</span>
                )}
            </div>
        );
    }

    return (
        <div className="verify__strip">
            <div className="verify__strip-head">
                <b>Send this once, from your phone</b>
                <Countdown seconds={wa.secondsRemaining} />
            </div>
            <code className="verify__code">{wa.message}</code>
            <p>
                It goes to {wa.transaction.display_number} — Lemma&rsquo;s verification number, not this
                teammate. Whichever phone sends it becomes the number you are known by here.
            </p>
            <div className="verify__acts">
                <OpenWhatsApp url={wa.transaction.whatsapp_url} />
                <CopyMessage state={wa.copied} onCopy={wa.copy} />
            </div>
        </div>
    );
}
