"use client";

import { useState } from "react";
import { sayCap } from "@/data/contacts";
import { isForbidden } from "@/session/auth-state";
import { useCapChange, useContactsCap } from "@/space/contact-queries";

/** What answering people outside the organization may cost it a month.
 *
 *  Contacts are never billed, so this is the ceiling on what they can cost.
 *  Past it, the organization's bots stop answering contacts until the month
 *  turns, and tell them a person will reply. Owners and editors set it, in
 *  one row of the organization's Usage: the cap as it stands, and the
 *  field that changes it. */
export function ContactsCapBlock({ orgId }: { orgId: string }) {
    const cap = useContactsCap(orgId);
    const change = useCapChange(orgId);
    const [typed, setTyped] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);

    if (cap.isPending) return <p className="kset-note" role="status">Reading the cap on answering contacts…</p>;
    if (!cap.data) {
        return isForbidden(cap.error)
            ? <p className="kset-note">Only owners and editors see the cap on answering contacts.</p>
            : (
                <p className="kset-said kset-said--bad" role="alert">
                    Couldn’t read the cap on answering contacts.
                    <button type="button" className="b b--s b--secondary" onClick={() => void cap.refetch()}>Try again</button>
                </p>
            );
    }
    const value = typed ?? (cap.data.limit == null ? "" : String(cap.data.limit));
    const save = (limit: number | null) => {
        setProblem(null);
        change.mutate(limit, {
            onSuccess: () => setTyped(null),
            onError: (error) => setProblem(isForbidden(error) ? "Only owners and editors can change it." : "Couldn’t save it. Try again."),
        });
    };

    return (
        <>
            <p className="ksub">Answering contacts <span>Contacts are never billed; this is the most they may cost a month</span></p>
            <div className="klist">
                <form
                    className="krow krow--kv"
                    onSubmit={(event) => {
                        event.preventDefault();
                        const amount = value.trim() === "" ? null : Number(value);
                        if (amount !== null && (!Number.isFinite(amount) || amount < 0)) {
                            setProblem("Enter an amount in dollars, or leave it empty for no cap.");
                            return;
                        }
                        save(amount);
                    }}
                >
                    <label className="krow__k" htmlFor={"contacts-cap-" + orgId}>Monthly cap</label>
                    <span className="krow__v kset-wrap">
                        <b>{sayCap(cap.data)}</b>
                        <input
                            id={"contacts-cap-" + orgId}
                            className="kinput kset-input"
                            inputMode="decimal"
                            value={value}
                            placeholder="No cap, in dollars"
                            onChange={(event) => setTyped(event.target.value)}
                        />
                        {problem && <span className="kset-said--bad" role="alert">{problem}</span>}
                    </span>
                    <span className="krow__end">
                        <button type="submit" className="b b--s b--secondary" disabled={change.isPending || typed === null}>
                            {change.isPending ? "Saving…" : "Save"}
                        </button>
                    </span>
                </form>
            </div>
        </>
    );
}
