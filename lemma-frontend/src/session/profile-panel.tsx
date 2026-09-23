"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { UserResponse } from "lemma-sdk";
import { CheckCircleIcon } from "@/ui/icons";
import { lemma } from "./client";
import { isCompleteMobileNumber, normalizeMobileNumber, storedMobileNumber } from "./mobile-number";
import { VerifyMobile } from "./mobile-verification";
import {
    changes, draftOf, hasChanges, localTimezone, problems, timezones,
    type ProfileDraft,
} from "./profile-edit";

/** The profile, edited here rather than sent somewhere else.
 *
 *  Not a read-only list and a link out to the platform. That leaves the one
 *  field this app actually depends on — the timezone — settable only in a
 *  different app. Every time somebody is told when a run finished or when a
 *  schedule fires, that is the field being read.
 */
export function ProfilePanel({ user }: { user: UserResponse }) {
    const server = useMemo(() => draftOf(user), [user]);
    const [draft, setDraft] = useState<ProfileDraft>(server);
    const [saved, setSaved] = useState(false);
    const queryClient = useQueryClient();

    /* A profile that changed elsewhere — another tab, the platform — rebases
       the form under the person, but only where they have not started typing.
       Overwriting an edit in progress to show a fresh copy is the rudest thing
       a form can do. */
    useEffect(() => {
        setDraft((current) => (hasChanges(server, current) ? current : server));
    }, [server]);

    const wrong = problems(draft);
    const dirty = hasChanges(server, draft);
    const zones = useMemo(() => timezones(), []);
    const here = localTimezone();

    /* The stamp belongs to the number the server holds, not to whatever is in
       the box. Saving a different number clears `mobile_verified_at` anyway —
       but only on save, so between the first keystroke and the button the
       badge would sit beside a number nobody has proved. */
    const stored = storedMobileNumber(user.mobile_number ?? "");
    const typed = normalizeMobileNumber(draft.mobile_number);
    const verified = Boolean(user.mobile_verified_at) && Boolean(typed) && typed === stored;

    const save = useMutation({
        mutationFn: () => lemma().users.upsertProfile(changes(server, draft)),
        onSuccess: (next) => {
            /* The server's answer replaces the cache rather than triggering a
               refetch: it already returned the saved user. */
            queryClient.setQueryData(["current-user"], next);
            setSaved(true);
        },
    });

    function set<K extends keyof ProfileDraft>(field: K, value: string) {
        setSaved(false);
        save.reset();
        setDraft((current) => ({ ...current, [field]: value }));
    }

    return (
        <form
            className="profile-form"
            onSubmit={(event) => { event.preventDefault(); if (dirty && !Object.keys(wrong).length) save.mutate(); }}
        >
            <div className="profile-form__pair">
                <label className="field">
                    <span>First name</span>
                    <input value={draft.first_name} onChange={(e) => set("first_name", e.target.value)} autoComplete="given-name" />
                </label>
                <label className="field">
                    <span>Last name</span>
                    <input value={draft.last_name} onChange={(e) => set("last_name", e.target.value)} autoComplete="family-name" />
                </label>
            </div>

            <label className="field">
                <span>Timezone</span>
                <select value={draft.timezone} onChange={(e) => set("timezone", e.target.value)}>
                    <option value="">Not set</option>
                    {/* A zone the server holds that this browser cannot name
                        still has to appear, or opening the form would quietly
                        change it to something else on the next save. */}
                    {draft.timezone && !zones.includes(draft.timezone) && <option value={draft.timezone}>{draft.timezone}</option>}
                    {zones.map((zone) => <option key={zone} value={zone}>{zone}</option>)}
                </select>
                {wrong.timezone && <em className="profile-form__problem">{wrong.timezone}</em>}
                {here && draft.timezone !== here && (
                    <button type="button" className="profile-form__hint" onClick={() => set("timezone", here)}>
                        Use {here}, where this browser is
                    </button>
                )}
            </label>

            <div className="profile-form__pair">
                <label className="field">
                    <span>Country</span>
                    <input value={draft.country} onChange={(e) => set("country", e.target.value)} autoComplete="country-name" />
                </label>
                <label className="field">
                    <span>Date of birth <em>optional</em></span>
                    <input type="date" value={draft.date_of_birth} onChange={(e) => set("date_of_birth", e.target.value)} />
                    {wrong.date_of_birth && <em className="profile-form__problem">{wrong.date_of_birth}</em>}
                </label>
            </div>

            <div className="profile-form__pair">
                <label className="field">
                    <span>Mobile number</span>
                    <input value={draft.mobile_number} onChange={(e) => set("mobile_number", e.target.value)} autoComplete="tel" inputMode="tel" />
                </label>
                <label className="field">
                    <span>Telegram</span>
                    <input
                        value={draft.telegram_username ? "@" + draft.telegram_username : ""}
                        onChange={(e) => set("telegram_username", e.target.value)}
                        placeholder="@handle"
                    />
                </label>
            </div>

            {/* Below the pair rather than inside it: a QR and three lines of
                explanation in a half-width column is a column of one word per
                line. */}
            {verified ? (
                <p className="profile-form__verified" role="status">
                    <CheckCircleIcon size={15} /> This number is verified.
                </p>
            ) : (
                <VerifyMobile
                    number={typed}
                    complete={isCompleteMobileNumber(typed)}
                    onVerified={(next) => {
                        /* The server wrote the number as part of verifying it,
                           so the box has to be told. Only this field: the rest
                           of the form may be mid-edit and none of it was
                           touched by what just happened. */
                        setSaved(false);
                        save.reset();
                        setDraft((current) => ({ ...current, mobile_number: (next.mobile_number ?? "").trim() }));
                    }}
                />
            )}

            <div className="profile-form__actions">
                <button className="btn btn--primary" type="submit" disabled={!dirty || save.isPending || Object.keys(wrong).length > 0}>
                    {save.isPending ? "Saving…" : "Save changes"}
                </button>
                {dirty && !save.isPending && (
                    <button className="btn" type="button" onClick={() => { setDraft(server); save.reset(); }}>Discard</button>
                )}
                {save.isError && <span className="profile-form__problem" role="alert">That did not save. {(save.error as Error)?.message}</span>}
                {saved && !dirty && <span className="profile-form__saved" role="status">Saved</span>}
            </div>
        </form>
    );
}
