"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { UserResponse } from "lemma-sdk";
import { source } from "@/data";
import { apiUrl, askApi, lemma, sameSiteWithApi } from "./client";
import { VERIFICATION_POLL_MS, verificationMessage } from "./mobile-number";

/** Proving a mobile number is yours, without a presentation.
 *
 *  Two channels and three places want this, and none of them can look the
 *  same. Account settings has a panel to spend and shows the number, the
 *  message and a QR; the reach sheet has a strip under an address, where a
 *  second QR beside the agent's own would read as the same code twice. So the
 *  transactions live here — start, poll while the tab is visible, expire on
 *  the clock — and each caller draws them.
 *
 *  None of these routes are on the SDK: they are `include_in_schema=False` on
 *  the backend and never reached the generated client, which is exactly what
 *  `askApi` is for.
 */

/* ------------------------------------------------------------------ */
/* What this deployment can actually do                                */
/* ------------------------------------------------------------------ */

export interface WhatsAppVerificationConfig {
    available: boolean;
    display_number?: string | null;
}

/** Whether a number can be verified over WhatsApp here at all.
 *
 *  `auth_whatsapp_mobile_verification_enabled` is off by default and four
 *  WhatsApp credentials have to be set beside it, so "no" is the ordinary
 *  answer and every caller has to be ready to draw nothing. */
export function useWhatsAppVerificationConfig() {
    return useQuery({
        queryKey: ["mobile-verification", "whatsapp"],
        enabled: source.label !== "sample",
        staleTime: 5 * 60_000,
        queryFn: async (): Promise<WhatsAppVerificationConfig> => {
            try {
                return await askApi<WhatsAppVerificationConfig>(
                    "/auth/mobile-verification/whatsapp/config",
                );
            } catch {
                /* Unreachable and switched off are the same thing to a button
                   that would otherwise 503 on the first click. */
                return { available: false };
            }
        },
    });
}

export interface TelegramVerificationConfig {
    /** The deployment has Telegram's OIDC client configured. */
    enabled: boolean;
    /** Whether this page is first-party to the API, so a top-level hand-off
     *  carries the session cookie. Half of the answer rather than a detail —
     *  `useTelegramMobileVerification` explains why. */
    sameSite: boolean;
}

export function useTelegramVerificationConfig() {
    return useQuery({
        queryKey: ["mobile-verification", "telegram"],
        enabled: source.label !== "sample",
        staleTime: 5 * 60_000,
        queryFn: async (): Promise<TelegramVerificationConfig> => {
            const sameSite = sameSiteWithApi();
            try {
                const said = await askApi<{ enabled?: boolean }>("/auth/telegram/config");
                return { enabled: said.enabled === true, sameSite };
            } catch {
                return { enabled: false, sameSite };
            }
        },
    });
}

/* ------------------------------------------------------------------ */
/* Shared plumbing                                                     */
/* ------------------------------------------------------------------ */

/** The API says why in the body, and `askApi` throws that body verbatim so
 *  nothing is lost on the way up. Nobody should read `{"detail":"…"}`. */
function reason(cause: unknown, fallback: string): string {
    const said = cause instanceof Error ? cause.message.trim() : "";
    if (!said) return fallback;
    try {
        const parsed = JSON.parse(said) as { detail?: unknown; message?: unknown };
        const detail = parsed.detail ?? parsed.message;
        if (typeof detail === "string" && detail.trim()) return detail.trim();
    } catch {
        /* Not JSON, so the text is already the sentence. */
    }
    return said.startsWith("{") || said.startsWith("[") ? fallback : said;
}

/** Poll, but only while somebody is looking.
 *
 *  A verification arrives by webhook and there is nothing to subscribe to, so
 *  this asks. It stops when the tab is hidden and asks once immediately on the
 *  way back, because the interesting moment is exactly the one where somebody
 *  returns from WhatsApp — polling a background tab for minutes to be ready
 *  for that is the same answer for more money. */
function useVisiblePolling(active: boolean, check: () => void | Promise<void>) {
    const ask = useRef(check);
    ask.current = check;
    useEffect(() => {
        if (!active) return;
        let poll: number | null = null;
        const begin = () => {
            if (poll !== null) window.clearInterval(poll);
            if (document.visibilityState !== "visible") return;
            void ask.current();
            poll = window.setInterval(() => void ask.current(), VERIFICATION_POLL_MS);
        };
        begin();
        document.addEventListener("visibilitychange", begin);
        return () => {
            if (poll !== null) window.clearInterval(poll);
            document.removeEventListener("visibilitychange", begin);
        };
    }, [active]);
}

/** Take the newly verified user into the cache the whole app reads.
 *
 *  Written rather than invalidated, the way saving the profile does it: the
 *  answer is already in hand and a refetch would ask for what we are holding. */
function useSettle(onVerified: (user: UserResponse) => void) {
    const queryClient = useQueryClient();
    const tell = useRef(onVerified);
    tell.current = onVerified;
    return useCallback(async () => {
        const fresh = await lemma().users.current();
        queryClient.setQueryData(["current-user"], fresh);
        tell.current(fresh);
    }, [queryClient]);
}

/* ------------------------------------------------------------------ */
/* WhatsApp                                                            */
/* ------------------------------------------------------------------ */

/** Idle, copied, or refused by the browser. */
export type CopyState = "idle" | "done" | "failed";

export interface WhatsAppTransaction {
    transaction_id: string;
    code: string;
    /** `https://wa.me/<Lemma's number>?text=LEMMA%20VERIFY%20<code>`. The
     *  prefilled text is the whole point: the code has to arrive inside the
     *  message and nobody retypes ten characters of it correctly. */
    whatsapp_url: string;
    display_number: string;
    expires_at: string;
}

export function useWhatsAppMobileVerification({
    onVerified,
}: {
    onVerified: (user: UserResponse) => void;
}) {
    const [transaction, setTransaction] = useState<WhatsAppTransaction | null>(null);
    const [starting, setStarting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [copied, setCopied] = useState<CopyState>("idle");
    const [now, setNow] = useState(() => Date.now());
    const settle = useSettle(onVerified);
    const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(copyTimer.current), []);

    const expiresAt = transaction ? Date.parse(transaction.expires_at) : 0;
    const secondsRemaining = transaction ? Math.max(0, Math.ceil((expiresAt - now) / 1000)) : 0;

    const expire = useCallback(() => {
        setTransaction(null);
        setError("That code expired. Start again for a fresh one.");
    }, []);

    useEffect(() => {
        if (!transaction) return;
        const tick = window.setInterval(() => {
            const current = Date.now();
            setNow(current);
            if (current >= expiresAt) expire();
        }, 1000);
        return () => window.clearInterval(tick);
    }, [expire, expiresAt, transaction]);

    const id = transaction?.transaction_id;
    useVisiblePolling(
        Boolean(id),
        useCallback(async () => {
            if (!id) return;
            try {
                const answer = await askApi<{ status: "PENDING" | "VERIFIED" | "EXPIRED" }>(
                    "/auth/mobile-verification/whatsapp/status/" + encodeURIComponent(id),
                );
                if (answer.status === "VERIFIED") {
                    setTransaction(null);
                    await settle();
                } else if (answer.status === "EXPIRED") {
                    expire();
                }
            } catch {
                /* Best effort. A status route that is briefly unreachable is
                   not a verification that failed, and the next tick asks. */
            }
        }, [expire, id, settle]),
    );

    const message = useMemo(
        () => (transaction ? verificationMessage(transaction.code) : ""),
        [transaction],
    );

    /* A copy that failed is worth a word. Everywhere else in this app a
       refused clipboard is swallowed and the button simply does nothing, which
       is survivable for an address somebody can read off the screen — but the
       thing being copied here is ten characters of code that has to arrive
       exactly, and "nothing happened" is the worst possible answer. The
       message stays selectable in one gesture, so the failure has somewhere to
       point. */
    const copy = useCallback(() => {
        clearTimeout(copyTimer.current);
        const settleCopy = (state: CopyState) => {
            setCopied(state);
            copyTimer.current = setTimeout(() => setCopied("idle"), 2400);
        };
        const writing = navigator.clipboard?.writeText(message);
        if (!writing) {
            settleCopy("failed");
            return;
        }
        writing.then(() => settleCopy("done")).catch(() => settleCopy("failed"));
    }, [message]);

    /** Omit the number to bind whichever phone answers.
     *
     *  Account settings has one to declare because the form asked for it, and
     *  declaring it catches "somebody else already owns this" up front. The
     *  reach sheet does not, and the backend treats an undeclared transaction
     *  as "the sender is the answer". */
    const start = useCallback(async (mobileNumber?: string) => {
        setStarting(true);
        setError(null);
        try {
            const created = await askApi<WhatsAppTransaction>(
                "/auth/mobile-verification/whatsapp/start",
                { method: "POST", body: JSON.stringify(mobileNumber ? { mobile_number: mobileNumber } : {}) },
            );
            setTransaction(created);
            setNow(Date.now());
        } catch (cause) {
            setError(reason(cause, "Verification could not be started."));
        } finally {
            setStarting(false);
        }
    }, []);

    const cancel = useCallback(() => {
        setTransaction(null);
        setError(null);
    }, []);

    return { transaction, starting, error, secondsRemaining, message, copied, start, cancel, copy };
}

/* ------------------------------------------------------------------ */
/* Telegram                                                            */
/* ------------------------------------------------------------------ */

/** The backend's own transaction lives five minutes; waiting longer than it
 *  can be answered is a spinner that will never stop. */
const TELEGRAM_WAIT_MS = 5 * 60_000;

/** Telegram's hand-off, which is somebody else's page and then back.
 *
 *  Two things make this different from the platform's version of it, and both
 *  come from where this app is served.
 *
 *  It opens a tab rather than navigating. Settings here is a modal held in
 *  React state, so leaving the page and coming back would land on the app with
 *  the dialog shut and nothing to say what had happened. And `safe_return_to`
 *  only honours the platform's own two origins, so "back" would not be here
 *  anyway — the tab finishes on Lemma's web app. `return_to` is still sent, to
 *  be right on the day this origin is allowed.
 *
 *  It needs the session cookie. `/auth/telegram/start` is a top-level
 *  navigation and a navigation carries no Authorization header, so a browser
 *  holding only a bearer token gets a 401 from a button that looked fine. That
 *  is what `sameSite` is for: somewhere to say so instead.
 */
export function useTelegramMobileVerification({
    onVerified,
}: {
    onVerified: (user: UserResponse) => void;
}) {
    const [waiting, setWaiting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const deadline = useRef(0);
    const settle = useSettle(onVerified);

    useVisiblePolling(
        waiting,
        useCallback(async () => {
            if (Date.now() > deadline.current) {
                setWaiting(false);
                setError("That did not finish in Telegram. Try again when you are ready.");
                return;
            }
            try {
                const fresh = await lemma().users.current();
                if (!fresh.mobile_verified_at) return;
                setWaiting(false);
                await settle();
            } catch {
                /* Best effort; the next tick asks again. */
            }
        }, [settle]),
    );

    const start = useCallback(() => {
        setError(null);
        const url = new URL(apiUrl() + "/auth/telegram/start");
        url.searchParams.set("purpose", "verify_mobile");
        url.searchParams.set("return_to", window.location.href);
        const tab = window.open(url.toString(), "_blank", "noopener,noreferrer");
        if (!tab) {
            setError("Your browser blocked that tab. Allow pop-ups for this site and try again.");
            return;
        }
        deadline.current = Date.now() + TELEGRAM_WAIT_MS;
        setWaiting(true);
    }, []);

    const cancel = useCallback(() => {
        setWaiting(false);
        setError(null);
    }, []);

    return { waiting, error, start, cancel };
}
