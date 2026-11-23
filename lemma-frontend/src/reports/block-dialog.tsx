"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Modal } from "@/shell/modal";
import { problemOf } from "@/channels/problem";
import { block, setContactBlocked } from "./api";
import {
    blockLine,
    blockTitle,
    memberBlockNote,
    stopAnsweringLabel,
    stopAnsweringLine,
    type Blockable,
} from "./model";

/** Blocking a person, for the reader alone. Somebody who may change the
 *  Lemma is also offered to stop it answering a contact, which is the
 *  contact's own setting and holds for everybody. Every chat of the Lemma
 *  is read again afterwards, so the person's lines go from all of them. */
export function BlockDialog({ podId, person, lemma, mayStopAnswering, onClose }: {
    podId: string;
    person: Blockable;
    /** The Lemma's name, as its chats call it. */
    lemma: string;
    /** The reader may change the Lemma's contacts (`pod.update`). */
    mayStopAnswering: boolean;
    onClose: () => void;
}) {
    const cache = useQueryClient();
    const offerStop = mayStopAnswering && person.kind === "CONTACT";
    const [stop, setStop] = useState(false);
    const save = useMutation({
        mutationFn: async () => {
            await block(podId, person);
            if (offerStop && stop) await setContactBlocked(podId, person.id, true);
        },
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["channel", podId] });
            onClose();
        },
    });
    const busy = save.isPending;
    const close = () => { if (!busy) onClose(); };
    return (
        <Modal
            title={blockTitle(person)}
            narrow
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" disabled={busy} onClick={close}>Cancel</button>
                <button type="button" className="b b--l b--primary" disabled={busy} onClick={() => save.mutate()}>
                    {busy ? "Blocking…" : "Block"}
                </button>
            </>}
        >
            <div className="kd-stack">
                <p className="kd-line">{blockLine(person, lemma)}</p>
                {offerStop && (
                    <div className="kd-block-stop">
                        <span className="kd-block-stop__words">
                            <span className="kd-block-stop__t">{stopAnsweringLabel(person, lemma)}</span>
                            <span className="kd-small">{stopAnsweringLine(person, lemma)}</span>
                        </span>
                        <button
                            type="button"
                            className="kswitch"
                            role="switch"
                            aria-label={stopAnsweringLabel(person, lemma)}
                            aria-checked={stop}
                            disabled={busy}
                            onClick={() => setStop((was) => !was)}
                        />
                    </div>
                )}
                {person.kind === "MEMBER" && <p className="kd-small">{memberBlockNote(person, lemma)}</p>}
                {save.isError && <p className="kd-problem" role="alert">{problemOf(save.error, "Couldn’t block them.")}</p>}
            </div>
        </Modal>
    );
}
