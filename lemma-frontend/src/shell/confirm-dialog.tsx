"use client";

import type { ReactNode } from "react";
import { Modal } from "./modal";

/** One way to ask before something that cannot be taken back.
 *
 *  There were five: a browser `confirm()` in People, and a row that turned
 *  into a question in place on a run, a file, an agent and a browser login,
 *  each with its own buttons in its own order. A question that changes shape
 *  every time is one people stop reading, so they are all this now: the kit's
 *  yes-or-no dialog, its title the question, one line under it saying what
 *  happens, and Cancel then the act at its foot, the act in red when it
 *  removes something.
 *
 *  It stays open until the act has an answer: `busy` holds it (no Escape, no
 *  click outside), and a refusal is said inside it, in `problem`, where the
 *  person who asked is still looking. The caller closes it on success. */
export function ConfirmDialog({ title, line, confirm, cancel = "Cancel", working, danger = false, busy = false, problem = null, onConfirm, onClose }: {
    /** The question, naming what it is about: "Delete Q4 pipeline.xlsx?". */
    title: string;
    /** What happens, in one line. */
    line?: ReactNode;
    /** The act, as its button says it: "Delete", "Sign out". */
    confirm: string;
    /** The way back, when "Cancel" is not the word: "Keep it". */
    cancel?: string;
    /** The act's button while it runs. "Deleting…" reads better than a
     *  spinner in a button that says what is happening. */
    working?: string;
    /** It removes something, so its button is red. */
    danger?: boolean;
    busy?: boolean;
    /** Why the act did not happen, said in the dialog. */
    problem?: ReactNode;
    onConfirm: () => void;
    onClose: () => void;
}) {
    /* Closing mid-request would leave it running with nobody to hear how it
       ended. */
    const close = () => { if (!busy) onClose(); };
    return (
        <Modal
            title={title}
            narrow
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" disabled={busy} onClick={close}>{cancel}</button>
                <button type="button" className={"b b--l " + (danger ? "b--danger" : "b--primary")} disabled={busy} onClick={onConfirm}>
                    {busy ? working ?? confirm + "…" : confirm}
                </button>
            </>}
        >
            <div className="kd-confirm">
                {line && <p className="kd-line">{line}</p>}
                {problem && <p className="kd-problem" role="alert">{problem}</p>}
            </div>
        </Modal>
    );
}
