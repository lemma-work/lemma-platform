import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UserProfileRequest, UserResponse } from "lemma-sdk";
import { useCacheKey } from "../../../data/hooks";
import type { Me } from "../../../data/types";
import { useSession } from "../../../session/session";
import { copyText } from "../../../thread/clipboard";
import { adminKey, useAdminSource } from "../hooks";
import type { VerifyTransaction } from "./source";
import { VERIFY_POLL_MS, reasonOf, secondsLeft } from "./verify";
import { verificationMessage } from "@web/session/mobile-number";
import { displayName } from "@web/session/profile-edit";

/** This part's reads and writes, through react-query. Key every query with
 *  `adminKey(useCacheKey(), "account", …)`. */
export function useAccountSource() {
    return useAdminSource().account;
}

/** The signed-in person, as the profile endpoint holds them. */
export function useProfile() {
    const source = useAccountSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "account", "profile"), queryFn: () => source.profile(), staleTime: 5 * 60_000 });
}

/** Take a fresh profile into every place that shows it: this part's cache,
 *  and the name the account screen and Home greet the person by. */
function useKeepProfile() {
    const cache = useQueryClient();
    const key = useCacheKey();
    const { mode } = useSession();
    return useCallback((user: UserResponse) => {
        cache.setQueryData(adminKey(key, "account", "profile"), user);
        cache.setQueryData<Me>([key, "me"], (held) => held ? { ...held, name: displayName(user, held.name), firstName: user.first_name?.trim() || held.firstName } : held);
        /* Live, the server's own reading of the name follows; sample data's
           person is the one just written. */
        if (mode !== "sample") void cache.invalidateQueries({ queryKey: [key, "me"] });
    }, [cache, key, mode]);
}

/** Save only what changed (the web's `changes`). */
export function useSaveProfile() {
    const source = useAccountSource();
    const keep = useKeepProfile();
    return useMutation({ mutationFn: (patch: UserProfileRequest) => source.saveProfile(patch), onSuccess: keep });
}

/** Whether this server verifies numbers over WhatsApp. */
export function useVerifyConfig() {
    const source = useAccountSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "account", "verify-config"), queryFn: () => source.whatsappVerification(), staleTime: 5 * 60_000 });
}

export function useTelegramVerification() {
    const source = useAccountSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "account", "telegram-config"), queryFn: () => source.telegramVerification(), staleTime: 5 * 60_000 });
}

/** Ask while the person can be looking: every few seconds while the app is
 *  in front, and at once when they come back to it, which is the moment the
 *  message has usually just been sent. */
function useWhileActive(active: boolean, check: () => void | Promise<void>) {
    const ask = useRef(check);
    ask.current = check;
    useEffect(() => {
        if (!active) return;
        let timer: ReturnType<typeof setInterval> | null = null;
        const begin = () => {
            if (timer) clearInterval(timer);
            timer = null;
            if (AppState.currentState !== "active") return;
            void ask.current();
            timer = setInterval(() => void ask.current(), VERIFY_POLL_MS);
        };
        begin();
        const listening = AppState.addEventListener("change", begin);
        return () => {
            if (timer) clearInterval(timer);
            listening.remove();
        };
    }, [active]);
}

/** One WhatsApp verification, from the code to the proved number (the
 *  web's `useWhatsAppMobileVerification`): start it, count it down, ask
 *  after it while the app is open, and on success take the person's fresh
 *  profile everywhere and hand it to `onVerified`. */
export function useWhatsAppVerification(onVerified: (user: UserResponse) => void) {
    const source = useAccountSource();
    const keep = useKeepProfile();
    const [transaction, setTransaction] = useState<VerifyTransaction | null>(null);
    const [starting, setStarting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const [now, setNow] = useState(() => Date.now());
    const tell = useRef(onVerified);
    tell.current = onVerified;

    const expire = useCallback(() => {
        setTransaction(null);
        setError("That code ran out. Start again for a fresh one.");
    }, []);

    useEffect(() => {
        if (!transaction) return;
        const tick = setInterval(() => {
            const current = Date.now();
            setNow(current);
            if (secondsLeft(transaction.expiresAt, current) <= 0) expire();
        }, 1000);
        return () => clearInterval(tick);
    }, [transaction, expire]);

    const id = transaction?.id;
    useWhileActive(Boolean(id), async () => {
        if (!id) return;
        try {
            const status = await source.verificationStatus(id);
            if (status === "VERIFIED") {
                setTransaction(null);
                const fresh = await source.profile();
                keep(fresh);
                tell.current(fresh);
            } else if (status === "EXPIRED") {
                expire();
            }
        } catch {
            /* A check that did not get through is not a verification that
               failed; the next one asks again. */
        }
    });

    /** Without a number, whichever phone sends the code becomes the number. */
    const start = useCallback(async (mobileNumber?: string) => {
        setStarting(true);
        setError(null);
        try {
            const made = await source.startWhatsAppVerification(mobileNumber);
            setTransaction(made);
            setNow(Date.now());
        } catch (problem) {
            setError(reasonOf(problem, "Verification could not be started. Try again."));
        } finally {
            setStarting(false);
        }
    }, [source]);

    const cancel = useCallback(() => {
        setTransaction(null);
        setError(null);
    }, []);

    const message = transaction ? verificationMessage(transaction.code) : "";
    const copy = useCallback(() => {
        if (!message) return;
        copyText(message);
        setCopied(true);
        setTimeout(() => setCopied(false), 2400);
    }, [message]);

    return {
        transaction,
        starting,
        error,
        message,
        copied,
        seconds: transaction ? secondsLeft(transaction.expiresAt, now) : 0,
        start,
        cancel,
        copy,
    };
}
