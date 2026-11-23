"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DELETE_POD, source, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { isForbidden } from "@/session/auth-state";
import { isLandingPreview } from "@/marketing/preview-mode";
import { Modal } from "@/shell/modal";
import { namesTeammate } from "./confirm-name";

/** Whether the signed-in person may delete this teammate.
 *
 *  Asked of `podPermissions.me` — whether they hold `pod.delete` here — and
 *  under the same query key `useCanManageMembers` uses, so the About page asks
 *  the server once for both. A pod admin holds it; so does the organization's
 *  owner, who may not be a member at all, which is why no role is read here.
 *  Unknown counts as no.
 *
 *  The sample source deletes for real, in memory, and has no server to ask,
 *  so there the answer is yes. The landing's preview never offers it: that
 *  page is a tour of somebody else's team, and nothing on it is for undoing. */
function useMayDelete(podId: string): boolean {
    const preview = isLandingPreview();
    const sample = source.label === "sample";
    const permissions = useQuery({
        queryKey: ["pod-permissions", podId],
        queryFn: () => lemma(podId).podPermissions.me(podId),
        enabled: !preview && !sample,
        staleTime: 60_000,
    });
    if (preview) return false;
    if (sample) return true;
    return (permissions.data?.actions ?? []).includes(DELETE_POD);
}

/** The last thing on About: the way to delete this teammate, for the people
 *  the server will let do it. Everybody else sees nothing — a section that
 *  only says "you can't" is a section about somebody else's permissions. */
export function DeleteTeammate({ pod, orgId, onDeleted }: {
    pod: Pod;
    orgId: string | null;
    /** Leave the space. Called once the server has said it is gone, before
     *  the lists are refreshed, so nothing redraws a teammate that is not
     *  there any more. */
    onDeleted: () => void;
}) {
    const may = useMayDelete(pod.id);
    const [asking, setAsking] = useState(false);
    if (!may) return null;
    return (
        <section className="aboutpage__delete" aria-label={"Delete " + pod.name}>
            <div>
                <h2>Delete {pod.name}</h2>
                <p>Everything in {pod.name}’s space goes with it, for everyone here.</p>
            </div>
            <button type="button" className="b b--m b--secondary b--red" onClick={() => setAsking(true)}>Delete</button>
            {asking && <ConfirmDelete pod={pod} orgId={orgId} onClose={() => setAsking(false)} onDeleted={onDeleted} />}
        </section>
    );
}

/** Deleting a Lemma: what goes, in the words of the things people will
 *  notice going, then its name to type. Typing it is the confirmation: a
 *  second "are you sure?" button is pressed by the same hand that pressed the
 *  first. Keep it, or Delete, at the foot; the dialog stays until the server
 *  answers. Exported for anywhere else that offers to delete one. */
export function ConfirmDelete({ pod, orgId, onClose, onDeleted }: {
    pod: Pod;
    orgId: string | null;
    onClose: () => void;
    onDeleted: () => void;
}) {
    const cache = useQueryClient();
    const [typed, setTyped] = useState("");
    /* Focused a task late on purpose: `Modal` focuses its own panel in an
       effect, and a child's effect runs before its parent's — so `autoFocus`
       or a focus here and now is undone in the same commit. */
    const field = useRef<HTMLInputElement>(null);
    useEffect(() => {
        const later = window.setTimeout(() => field.current?.focus(), 0);
        return () => window.clearTimeout(later);
    }, []);
    const named = namesTeammate(typed, pod.name);
    const remove = useMutation({
        mutationFn: () => source.deletePod(pod.id),
        onSuccess: () => {
            onClose();
            onDeleted();
            /* Every list a teammate appears in is under "pods" — the rail, the
               team page, the switcher — and those are what must stop drawing
               it. Its own reads are dropped rather than refetched: they would
               only come back as "not found". */
            void cache.invalidateQueries({ queryKey: ["pods"] });
            void cache.invalidateQueries({ queryKey: ["pods", orgId] });
            cache.removeQueries({ queryKey: ["pod-detail", pod.id] });
            cache.removeQueries({ queryKey: ["pod-access", pod.id] });
            cache.removeQueries({ queryKey: ["pod-permissions", pod.id] });
        },
    });
    const busy = remove.isPending;
    /* Closing mid-request would leave the request running with nobody to
       hear how it ended, so the dialog stays until the server answers. */
    const close = () => { if (!busy) onClose(); };
    const formId = "kd-delete-" + pod.id;

    return (
        <Modal
            title={"Delete " + pod.name + "?"}
            subtitle="This can’t be undone. Everything in it goes, for everyone in it."
            narrow
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" onClick={close} disabled={busy}>Keep it</button>
                <button type="submit" form={formId} className="b b--l b--danger" disabled={!named || busy}>
                    {busy ? "Deleting…" : "Delete " + pod.name}
                </button>
            </>}
        >
            <form
                id={formId}
                className="kd-stack"
                onSubmit={(event) => { event.preventDefault(); if (named && !busy) remove.mutate(); }}
            >
                <ul className="kd-gone">
                    <li>Its chats, pages, tables, files and apps can no longer be opened.</li>
                    <li>Its automations stop. Nothing it was set to do will run.</li>
                    <li>It stops answering in Slack, on WhatsApp and by email.</li>
                    <li>Its email address and phone number are released.</li>
                </ul>
                <label className="kfield">
                    <span>Type <b className="kd-name">{pod.name}</b> to delete it</span>
                    <input
                        className="kinput"
                        ref={field}
                        value={typed}
                        onChange={(event) => setTyped(event.target.value)}
                        autoComplete="off"
                        spellCheck={false}
                        disabled={busy}
                    />
                </label>
                {remove.isError && (
                    <p className="kd-problem" role="alert">
                        {isForbidden(remove.error)
                            ? "You may not delete " + pod.name + "."
                            : pod.name + " could not be deleted. Try again."}
                    </p>
                )}
            </form>
        </Modal>
    );
}
