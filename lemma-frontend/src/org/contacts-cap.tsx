"use client";

import { useState } from "react";
import { sayCap } from "@/data/contacts";
import { isForbidden } from "@/session/auth-state";
import { useCapChange, useContactsCap } from "@/space/contact-queries";

/** What answering people outside the organization may cost it a month.
 *
 *  Contacts are never billed, so this is the ceiling on what they can cost.
 *  Past it, the organization's bots stop answering contacts until the month
 *  turns, and tell them a person will reply. Owners and editors set it. */
export function ContactsCapBlock({ orgId }: { orgId: string }) {
    const cap = useContactsCap(orgId);
    const change = useCapChange(orgId);
    const [typed, setTyped] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);

    if (cap.isPending) return <p className="usage-quiet" role="status">Reading the cap…</p>;
    if (!cap.data) {
        return <p className="usage-quiet">{isForbidden(cap.error) ? "Only owners and editors see the contacts cap." : "Couldn’t read the contacts cap."}</p>;
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
        <section className="usage-block">
            <h4>Answering contacts</h4>
            <p className="usage-quiet">{sayCap(cap.data)}. Contacts are never billed; this is the most they may cost a month.</p>
            <form
                className="csheet__row"
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
                <label className="record-form__field">
                    <span className="smanage__label">Monthly cap (USD)</span>
                    <input inputMode="decimal" value={value} placeholder="No cap" onChange={(event) => setTyped(event.target.value)} />
                </label>
                <button type="submit" className="ghost-pill" disabled={change.isPending || typed === null}>
                    {change.isPending ? "Saving…" : "Save"}
                </button>
            </form>
            {problem && <p role="alert">{problem}</p>}
        </section>
    );
}
