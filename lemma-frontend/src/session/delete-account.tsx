"use client";

import { useId, useState } from "react";
import { ORG } from "@/copy";
import { lemma } from "@/session/client";
import { useSession } from "@/session/session";
import { Modal } from "@/shell/modal";
import {
    confirmsAccount,
    DELETED_PATH,
    DELETION_CONSEQUENCES,
    deletionRefusal,
    type DeletionRefusal,
} from "@/session/account-deletion";

/** Deleting your own account: what it does, said plainly, then the address
 *  typed back as the confirmation.
 *
 *  One form for every way in — the Account settings dialog and the public
 *  deletion page — so what a person is told before the button is the same
 *  wherever they found it. On success the session is already over on the
 *  server; signing out here only clears what this browser remembers, and
 *  lands on the public page saying the account is gone. */
export function DeleteAccountForm({ email, onCancel }: { email: string; onCancel?: () => void }) {
    const session = useSession();
    const inputId = useId();
    const [typed, setTyped] = useState("");
    const [busy, setBusy] = useState(false);
    const [refusal, setRefusal] = useState<DeletionRefusal | null>(null);
    const confirmed = confirmsAccount(typed, email);

    async function leave() {
        /* The server has ended the session; this clears this browser's copy of
           it. If that cannot be confirmed the account is still gone, so the
           page goes on regardless. */
        await session.signOut(DELETED_PATH).catch(() => window.location.assign(DELETED_PATH));
    }

    async function remove() {
        setBusy(true);
        setRefusal(null);
        try {
            await lemma().users.deleteAccount({ confirm_email: typed.trim() });
        } catch (problem) {
            const refused = deletionRefusal(problem);
            if (!refused.alreadyDeleted) {
                setRefusal(refused);
                setBusy(false);
                return;
            }
        }
        await leave();
    }

    return (
        <form
            className="delete-account"
            onSubmit={(event) => { event.preventDefault(); if (confirmed && !busy) void remove(); }}
        >
            <ul className="delete-account__what">
                {DELETION_CONSEQUENCES.map((line) => <li key={line}>{line}</li>)}
            </ul>
            <label className="field" htmlFor={inputId}>
                <span>Type <span className="delete-account__email">{email}</span> to confirm</span>
                <input
                    id={inputId}
                    value={typed}
                    onChange={(event) => setTyped(event.target.value)}
                    autoComplete="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    inputMode="email"
                    placeholder={email}
                    disabled={busy}
                />
            </label>
            {refusal && (
                <div className="delete-account__refused" role="alert">
                    <p>{refusal.message}</p>
                    {refusal.organizations.length > 0 && (
                        <p>Change who owns each under People, in its settings.</p>
                    )}
                </div>
            )}
            <div className="delete-account__actions">
                {onCancel && <button type="button" className="btn" onClick={onCancel} disabled={busy}>Keep my account</button>}
                <button type="submit" className="btn btn--danger" disabled={!confirmed || busy}>
                    {busy ? "Deleting…" : "Delete my account"}
                </button>
            </div>
        </form>
    );
}

/** The last thing in Account settings, apart from Signing out so neither is
 *  mistaken for the other. A dialog, because the explanation is the point
 *  and should have the screen to itself. */
export function DeleteAccountEntry({ email }: { email: string }) {
    const [open, setOpen] = useState(false);
    return (
        <div className="settings-delete">
            <div>
                <strong>Delete account</strong>
                <p>Erase your profile and leave every {ORG}. Work you shared stays with the people you shared it with.</p>
            </div>
            <button type="button" className="btn" onClick={() => setOpen(true)}>Delete account…</button>
            {open && (
                <Modal title="Delete your account" subtitle="This cannot be undone." narrow onClose={() => setOpen(false)}>
                    <DeleteAccountForm email={email} onCancel={() => setOpen(false)} />
                </Modal>
            )}
        </div>
    );
}
