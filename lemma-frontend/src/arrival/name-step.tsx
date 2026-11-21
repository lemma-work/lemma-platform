"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { nameAskedKey, settledKey } from "@/session/first-profile";
import { localTimezone, timezones } from "@/session/profile-edit";

/** "What should we call you?" -- asked by Lemma itself, first, before
 *  anything is made, so the Lemma can say hello by name on the next step.
 *
 *  The time zone is read off the browser and said once here, with a way to
 *  change it. Saved, the profile is settled: nothing asks the name or the
 *  time zone again over the Lemma they land in. */
export function NameStep({ firstName, onDone, onType }: { firstName: string; onDone: (name: string) => void; onType?: () => void }) {
    const queryClient = useQueryClient();
    const [name, setName] = useState(firstName);
    const zones = useMemo(() => timezones(), []);
    const [zone, setZone] = useState(() => localTimezone());
    const [changingZone, setChangingZone] = useState(false);

    const save = useMutation({
        mutationFn: async () => {
            const named = name.trim();
            const profile = {
                ...(named && named !== firstName.trim() ? { first_name: named } : {}),
                ...(zone ? { timezone: zone } : {}),
            };
            const next = Object.keys(profile).length > 0 ? await lemma().users.upsertProfile(profile) : null;
            return { named, next };
        },
        onSuccess: ({ named, next }) => {
            if (next) queryClient.setQueryData(["current-user"], next);
            const id = (next as { id?: string } | null)?.id ?? (queryClient.getQueryData(["current-user"]) as { id?: string } | undefined)?.id;
            if (id) {
                try {
                    localStorage.setItem(nameAskedKey(id), "1");
                    localStorage.setItem(settledKey(id), "1");
                } catch { /* asked once more, in the dialog */ }
            }
            onDone(named);
        },
    });

    return (
        <form
            className="ar__form"
            onSubmit={(event) => {
                event.preventDefault();
                if (!save.isPending) save.mutate();
            }}
        >
            <h1 className="ar__t">Welcome to Lemma. What should we call you?</h1>
            <label className="ar__big">
                <span className="ar__hidden">Your first name</span>
                <input value={name} autoFocus autoComplete="given-name" placeholder="Your first name" onChange={(event) => { save.reset(); setName(event.target.value); onType?.(); }} />
            </label>
            <p className="ar__zone">
                {changingZone ? (
                    <label className="ar__zone-pick">
                        <span>Time zone</span>
                        <select value={zone} autoFocus onChange={(event) => { save.reset(); setZone(event.target.value); }}>
                            {zone && !zones.includes(zone) && <option value={zone}>{zone}</option>}
                            {zones.map((one) => <option key={one} value={one}>{one}</option>)}
                        </select>
                    </label>
                ) : (
                    <>
                        <span>Time zone: {zone || "not set"}, from your browser</span>
                        <button type="button" className="ar__quiet" onClick={() => setChangingZone(true)}>Change</button>
                    </>
                )}
            </p>
            {save.isError && (
                <p className="ar__problem" role="alert">That didn’t save. {save.error instanceof Error ? save.error.message : ""}</p>
            )}
            <div className="ar__acts">
                <span className="ar__sum"><kbd>↵</kbd> to continue</span>
                {save.isError && <button type="button" className="ar__quiet" onClick={() => onDone(name.trim())}>Go on without saving</button>}
                <button type="submit" className="ar__btn ar__btn--primary" disabled={save.isPending}>
                    {save.isPending ? "Saving…" : name.trim() ? "Continue" : "Skip"}
                </button>
            </div>
        </form>
    );
}
