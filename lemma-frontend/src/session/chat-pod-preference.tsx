"use client";

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";

const PLATFORM = "WHATSAPP";

function problemOf(error: unknown): string {
    /* A 409 carries the server's own sentence — the pod already answers
       WhatsApp through its own number — and that sentence is the explanation. */
    const message = error instanceof Error ? error.message.trim() : "";
    return message || "That didn’t save. Try again.";
}

/** "WhatsApp answers from: [pod ▾]".
 *
 *  Lemma has one WhatsApp number for everybody, so which of the person's pods
 *  answers them there is theirs to say, and this is where they say it. Not on
 *  the profile form's Save button: the choice takes effect on its own, and
 *  tying it to unrelated fields would make a timezone edit look like it moved
 *  their chat.
 *
 *  Absent, not empty, for someone with nothing to choose between.
 */
export function ChatPodPreference() {
    const queryClient = useQueryClient();
    const id = useId();
    const key = ["chat-pod", PLATFORM];
    const choice = useQuery({ queryKey: key, queryFn: () => source.chatPodChoice(PLATFORM), staleTime: 60_000 });
    const [picked, setPicked] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    const save = useMutation({
        mutationFn: (podId: string) => source.setChatPod(PLATFORM, podId),
        onSuccess: (_, podId) => {
            queryClient.setQueryData(key, (current: typeof choice.data) => current ? { ...current, currentPodId: podId } : current);
            /* The reach screens read the same surfaces, and a pod that had
               none may have just been given one. */
            void queryClient.invalidateQueries({ queryKey: ["my-surfaces"] });
            void queryClient.invalidateQueries({ queryKey: key });
            setPicked(null);
            setSaved(true);
        },
        /* Back to what actually answers: the select must not keep showing a
           pod the server refused. */
        onError: () => setPicked(null),
    });

    if (choice.isPending) {
        return <p className="chat-pod chat-pod--quiet" role="status">Checking which pod answers you on WhatsApp…</p>;
    }
    /* Not knowing is not worth a red line in somebody's profile; the control
       is a convenience, and the next open of settings asks again. */
    if (choice.isError || !choice.data) return null;

    const { currentPodId, options } = choice.data;
    const value = picked ?? currentPodId ?? "";

    return (
        <div className="chat-pod">
            <label className="field chat-pod__field" htmlFor={id}>
                <span>WhatsApp answers from</span>
                <select
                    id={id}
                    value={value}
                    disabled={save.isPending}
                    onChange={(event) => {
                        const next = event.target.value;
                        if (!next || next === currentPodId) return;
                        setSaved(false);
                        setPicked(next);
                        save.mutate(next);
                    }}
                >
                    {!currentPodId && <option value="" disabled>Choose a pod</option>}
                    {options.map((option) => <option key={option.podId} value={option.podId}>{option.label}</option>)}
                </select>
            </label>
            <p className="profile-form__hint-line">Messages to Lemma on WhatsApp are answered by this workspace.</p>
            {save.isPending && <p className="chat-pod__status chat-pod__status--pending" role="status">Saving…</p>}
            {save.isError && <p className="profile-form__problem chat-pod__status" role="alert">{problemOf(save.error)}</p>}
            {saved && !save.isPending && !save.isError && <p className="profile-form__saved chat-pod__status" role="status">Saved. Your next WhatsApp message goes here.</p>}
        </div>
    );
}
