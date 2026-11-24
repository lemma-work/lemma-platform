"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Modal } from "@/shell/modal";
import { problemOf } from "@/channels/problem";
import { sendReport } from "./api";
import {
    MAX_REPORT_NOTE,
    REPORTED_LINE,
    REPORT_REASONS,
    reportLine,
    reportTitle,
    type Blockable,
    type ReportReason,
    type ReportTarget,
} from "./model";

/** Reporting a reply or a message to the team that runs Lemma: why, as one
 *  of a few plain picks, and any words. Once it is sent the same dialog says
 *  so; for a person's message it offers to block them too, which opens the
 *  block question (`onBlock`). A refusal is said in the dialog, where the
 *  person who sent it is still looking. */
export function ReportDialog({ podId, target, author, person, onBlock, onClose }: {
    podId: string;
    target: ReportTarget;
    /** Who wrote it, for the title: "Report Sam's message". */
    author: string | null;
    /** The person who wrote it, when they could be blocked. */
    person?: Blockable | null;
    onBlock?: (person: Blockable) => void;
    onClose: () => void;
}) {
    const [reason, setReason] = useState<ReportReason | null>(null);
    const [note, setNote] = useState("");
    const send = useMutation({
        mutationFn: (why: ReportReason) => sendReport(podId, target, why, note),
    });
    const busy = send.isPending;
    const close = () => { if (!busy) onClose(); };

    if (send.isSuccess) {
        return (
            <Modal
                title="Reported"
                narrow
                onClose={onClose}
                footer={person && onBlock ? (
                    <>
                        <button type="button" className="b b--l b--secondary" onClick={() => onBlock(person)}>Block {person.name}</button>
                        <button type="button" className="b b--l b--primary" onClick={onClose}>Done</button>
                    </>
                ) : (
                    <button type="button" className="b b--l b--primary" onClick={onClose}>Done</button>
                )}
            >
                <div className="kd-confirm">
                    <p className="kd-line">{REPORTED_LINE}</p>
                    {person && onBlock && <p className="kd-small">Blocking hides {person.name}’s messages from you.</p>}
                </div>
            </Modal>
        );
    }

    return (
        <Modal
            title={reportTitle(target, author)}
            subtitle={reportLine(target)}
            narrow
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" disabled={busy} onClick={close}>Cancel</button>
                <button type="button" className="b b--l b--primary" disabled={busy || reason === null} onClick={() => reason && send.mutate(reason)}>
                    {busy ? "Reporting…" : "Report"}
                </button>
            </>}
        >
            <div className="kd-stack">
                <div className="kfield" role="radiogroup" aria-label="What is wrong with it?">
                    <span>What is wrong with it?</span>
                    <div className="kopts">
                        {REPORT_REASONS.map((option) => (
                            <label key={option.id} className="kopt kopt--compact">
                                <input
                                    type="radio"
                                    name="report-reason"
                                    value={option.id}
                                    checked={reason === option.id}
                                    disabled={busy}
                                    onChange={() => setReason(option.id)}
                                />
                                <span className="kopt__t">{option.label}</span>
                                <span className="kopt__dot" aria-hidden="true" />
                            </label>
                        ))}
                    </div>
                </div>
                <label className="kfield">
                    <span>Anything we should know? <small>Optional</small></span>
                    <textarea
                        className="kinput"
                        rows={3}
                        maxLength={MAX_REPORT_NOTE}
                        value={note}
                        disabled={busy}
                        onChange={(event) => setNote(event.target.value)}
                    />
                </label>
                {send.isError && <p className="kd-problem" role="alert">{problemOf(send.error, "Couldn’t send the report.")}</p>}
            </div>
        </Modal>
    );
}
