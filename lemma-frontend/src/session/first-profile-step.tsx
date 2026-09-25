"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UserResponse } from "lemma-sdk";
import { source } from "@/data";
import { MATE } from "@/copy";
import { isLandingPreview } from "@/marketing/preview-mode";
import { Modal } from "@/shell/modal";
import { CheckCircleIcon, RefreshIcon } from "@/ui/icons";
import { lemma } from "./client";
import { asksFirstProfile, firstProfileNeeds, settledKey } from "./first-profile";
import { Code, Countdown, OpenWhatsApp } from "./mobile-verification";
import { changes, draftOf } from "./profile-edit";
import { useWhatsAppMobileVerification, useWhatsAppVerificationConfig } from "./use-mobile-verification";

function readSettled(userId: string): boolean {
    try {
        return localStorage.getItem(settledKey(userId)) === "1";
    } catch {
        return false;
    }
}

function writeSettled(userId: string) {
    try {
        localStorage.setItem(settledKey(userId), "1");
    } catch {
        /* A browser that will not remember asks again next time, which is the
           whole cost. */
    }
}

/** Mounted once, above the app. Draws nothing unless `asksFirstProfile` says
 *  this person is new and missing something the step can supply. */
export function FirstProfileStep() {
    const live = source.label !== "sample" && !isLandingPreview();
    const user = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: live,
        staleTime: 5 * 60_000,
        gcTime: 30 * 60_000,
    });
    const whatsApp = useWhatsAppVerificationConfig();

    /* Decided once, when both answers are in, and then held. The step removes
       its own reason to exist — verifying the phone fills in `mobile_number`
       — and a dialog that re-derived itself would vanish the moment it
       succeeded, before the person saw it work or saved their name. */
    const [decision, setDecision] = useState<{ open: boolean; askPhone: boolean } | null>(null);
    const ready = live && user.data !== undefined && !whatsApp.isPending;
    if (decision === null && ready && user.data) {
        const canWhatsApp = whatsApp.data?.available === true;
        setDecision({
            open: asksFirstProfile(user.data, { whatsApp: canWhatsApp, settled: readSettled(user.data.id) }),
            askPhone: firstProfileNeeds(user.data, { whatsApp: canWhatsApp }).phone,
        });
    }

    if (!decision?.open || !user.data) return null;
    const userId = user.data.id;
    return (
        <FirstProfileDialog
            user={user.data}
            askPhone={decision.askPhone}
            onDone={() => {
                writeSettled(userId);
                setDecision({ ...decision, open: false });
            }}
        />
    );
}

function FirstProfileDialog({
    user,
    askPhone,
    onDone,
}: {
    user: UserResponse;
    askPhone: boolean;
    onDone: () => void;
}) {
    const queryClient = useQueryClient();
    const [first, setFirst] = useState(() => (user.first_name ?? "").trim());
    const [last, setLast] = useState(() => (user.last_name ?? "").trim());

    const save = useMutation({
        mutationFn: async () => {
            /* Against the user as it is now, not as it was when the dialog
               opened: verifying the phone rewrites the cached user, and only
               the two name fields are this form's to change. */
            const before = draftOf(user);
            const patch = changes(before, { ...before, first_name: first, last_name: last });
            if (Object.keys(patch).length === 0) return null;
            return lemma().users.upsertProfile(patch);
        },
        onSuccess: (next) => {
            if (next) queryClient.setQueryData(["current-user"], next);
            onDone();
        },
    });

    return (
        <Modal
            title="Before you start"
            subtitle={`Your ${MATE}s call you by this name.`}
            onClose={onDone}
        >
            <form
                className="profile-form firstprofile"
                onSubmit={(event) => {
                    event.preventDefault();
                    if (first.trim() && !save.isPending) save.mutate();
                }}
            >
                <div className="profile-form__pair">
                    <label className="field">
                        <span>First name</span>
                        <input
                            value={first}
                            onChange={(event) => { save.reset(); setFirst(event.target.value); }}
                            autoComplete="given-name"
                            required
                        />
                    </label>
                    <label className="field">
                        <span>Last name</span>
                        <input
                            value={last}
                            onChange={(event) => { save.reset(); setLast(event.target.value); }}
                            autoComplete="family-name"
                        />
                    </label>
                </div>

                {askPhone && <PhoneByWhatsApp />}

                {save.isError && (
                    <p className="profile-form__problem" role="alert">
                        That did not save. {(save.error as Error)?.message}
                    </p>
                )}
                <div className="modal__acts">
                    <button className="linkish" type="button" onClick={onDone}>Skip for now</button>
                    <button className="btn btn--primary" type="submit" disabled={!first.trim() || save.isPending}>
                        {save.isPending ? "Saving…" : "Continue"}
                    </button>
                </div>
            </form>
        </Modal>
    );
}

/** The phone, proved rather than typed.
 *
 *  A code is started as soon as this is on screen, so the QR is already there
 *  to scan: asking for a click first would be a button whose only job is to
 *  reveal the next button. Whichever phone sends the message is the number
 *  that gets bound, so nothing is typed and nothing can be mistyped. */
function PhoneByWhatsApp() {
    const [verified, setVerified] = useState<string | null>(null);
    const wa = useWhatsAppMobileVerification({
        onVerified: (next) => setVerified((next.mobile_number ?? "").trim() || null),
    });

    /* One start per mount and never an automatic retry: five starts in
       fifteen minutes is the whole allowance, and an effect that reran on its
       own failure would spend it in a second. */
    const started = useRef(false);
    const start = wa.start;
    useEffect(() => {
        if (started.current) return;
        started.current = true;
        void start();
    }, [start]);

    return (
        <section className="firstprofile__phone" aria-labelledby="firstprofile-phone">
            <div className="firstprofile__phone-head">
                <b id="firstprofile-phone">
                    <img className="channel-icon" src="/connector-logos/whatsapp.svg" width={15} height={15} alt="" aria-hidden="true" />
                    Your phone <em>optional</em>
                </b>
                {wa.transaction && <Countdown seconds={wa.secondsRemaining} />}
            </div>

            {verified ? (
                <p className="profile-form__verified" role="status">
                    <CheckCircleIcon size={15} /> {verified} is verified as yours.
                </p>
            ) : wa.transaction ? (
                <div className="firstprofile__send">
                    <div className="firstprofile__lines">
                        {/* A promise about later, not now. At sign-up no
                            teammate is on WhatsApp yet, so nothing will answer
                            this message; what it buys is that one put there
                            later already knows the sender. */}
                        <p>Prove it&rsquo;s yours, so any {MATE} you put on WhatsApp later knows it&rsquo;s you.</p>
                        <p className="firstprofile__small">
                            It sends <code className="verify__code">{wa.message}</code> to{" "}
                            <span className="firstprofile__number">{wa.transaction.display_number}</span>,
                            Lemma&rsquo;s verification number.
                        </p>
                        <div className="verify__acts">
                            <OpenWhatsApp url={wa.transaction.whatsapp_url} quiet />
                        </div>
                    </div>
                    <Code url={wa.transaction.whatsapp_url} />
                </div>
            ) : wa.error ? (
                <div className="firstprofile__lines">
                    <p className="profile-form__problem" role="alert">{wa.error}</p>
                    <div className="verify__acts">
                        <button className="btn" type="button" disabled={wa.starting} onClick={() => void wa.start()}>
                            {wa.starting ? "Preparing…" : "Get a new code"}
                        </button>
                    </div>
                </div>
            ) : (
                <span className="guided__wait"><RefreshIcon size={13} /> Preparing a code…</span>
            )}
        </section>
    );
}
