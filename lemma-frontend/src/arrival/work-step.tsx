"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Job } from "lemma-sdk";
import { lemma } from "@/session/client";
import { nameAskedKey } from "@/session/first-profile";
import { captureEvent } from "@/site/analytics/client";
import { JOBS, OTHER_LIMIT, workAnswer, type JobId, type WorkAnswer } from "./work";

/** "What do you do?" Nine chips, any number of them, and the person's own
 *  words for anything else. Saved on the person; skipping saves an empty
 *  answer, so the question is asked once whichever way it ends.
 *
 *  An account that came in with no name (an email code carries none) is asked
 *  for one here too, in one optional field, rather than by a dialog over the
 *  Lemma they land in: the same screen, the same look. */
export function WorkStep({ askName, onDone }: { askName: boolean; onDone: (answer: WorkAnswer) => void }) {
    const queryClient = useQueryClient();
    const [first, setFirst] = useState("");
    const [picked, setPicked] = useState<ReadonlySet<JobId>>(new Set());
    const [writing, setWriting] = useState(false);
    const [other, setOther] = useState("");

    const save = useMutation({
        mutationFn: async (answer: WorkAnswer) => {
            const named = first.trim();
            if (askName && named) await lemma().users.upsertProfile({ first_name: named });
            /* The server's ids, read off its own enum, so a job it does not know
               is a type error here rather than a refusal at sign-up. */
            const next = await lemma().users.setWork({ jobs: answer.jobs.map((id) => Job[id]), ...(answer.other ? { other: answer.other } : {}) });
            return { answer, next };
        },
        onSuccess: ({ answer, next }) => {
            queryClient.setQueryData(["current-user"], next);
            /* Asked here; the profile dialog does not ask the name again. */
            if (askName) {
                try { localStorage.setItem(nameAskedKey(next.id), "1"); } catch { /* asked once more, in the dialog */ }
            }
            captureEvent("onboarding.work_answered", { jobs: answer.jobs.length, other: Boolean(answer.other), skipped: answer.jobs.length === 0 && !answer.other });
            onDone(answer);
        },
    });

    const flip = (id: JobId) => {
        save.reset();
        setPicked((was) => {
            const next = new Set(was);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };
    const answer = workAnswer(picked, writing ? other : "");
    const said = answer.jobs.length > 0 || Boolean(answer.other);

    return (
        <form
            className="ar__form"
            onSubmit={(event) => {
                event.preventDefault();
                if (!save.isPending) save.mutate(answer);
            }}
        >
            <h1 className="ar__t">What do you do?</h1>
            <p className="ar__model">Pick any that fit. Lemma uses it to suggest what to set up first.</p>
            <div className="ar__chips" role="group" aria-label="What you do">
                {JOBS.map((job) => (
                    <button key={job.id} type="button" className="ar__chip" aria-pressed={picked.has(job.id)} onClick={() => flip(job.id)}>
                        {job.label}
                    </button>
                ))}
                <button type="button" className="ar__chip" aria-pressed={writing} onClick={() => { save.reset(); setWriting((was) => !was); }}>
                    Something else
                </button>
            </div>
            {writing && (
                <label className="ar__field">
                    <span>In your own words</span>
                    <input
                        value={other}
                        maxLength={OTHER_LIMIT}
                        autoFocus
                        placeholder="Run a restaurant, teach, write grants…"
                        onChange={(event) => { save.reset(); setOther(event.target.value); }}
                    />
                </label>
            )}
            {askName && (
                <label className="ar__field">
                    <span>Your first name</span>
                    <input
                        value={first}
                        autoComplete="given-name"
                        onChange={(event) => { save.reset(); setFirst(event.target.value); }}
                    />
                </label>
            )}
            {save.isError && (
                <p className="ar__problem" role="alert">
                    That didn’t save. {save.error instanceof Error ? save.error.message : ""} Try again, or go on without saving and you’ll be asked another time.
                </p>
            )}
            <div className="ar__acts">
                <button type="submit" className="ar__btn ar__btn--primary" disabled={!said || save.isPending}>
                    {save.isPending && said ? "Saving…" : "Continue"}
                </button>
                {/* The question is optional. A save that keeps failing must not
                    stand between somebody and the Lemma behind it, so after a
                    failure the quiet way out goes on without saving. */}
                {save.isError ? (
                    <button type="button" className="ar__quiet" onClick={() => onDone(answer)}>
                        Go on without saving
                    </button>
                ) : (
                    <button type="button" className="ar__quiet" disabled={save.isPending} onClick={() => save.mutate({ jobs: [], other: null })}>
                        Skip
                    </button>
                )}
            </div>
        </form>
    );
}
