"use client";

import { useState, type InputHTMLAttributes, type ReactNode } from "react";
import { BackIcon } from "./icons";

/** The pieces every form in a sheet, a dialog or a settings page is drawn
 *  from, so a credential asked for in one place looks like one asked for in
 *  any other (kit.css, "forms"). Left-aligned, a label above each field, the
 *  form as wide as the column it is in. */

/** What every settings field tells a browser and a password manager: this is
 *  not a sign-in to Lemma.
 *
 *  It is a service's settings, but a password box with a text box above it
 *  is a login form to Chrome, which filled a database's name and password
 *  with the person's own email and saved Lemma password. `off` on a text
 *  field and `new-password` on a secret are the two values Chrome does not
 *  fill a saved login into (it may offer to generate a password instead,
 *  which is harmless); the data attributes are the opt-outs 1Password,
 *  LastPass, Bitwarden and Dashlane read. */
export const NOT_A_LOGIN = {
    "data-1p-ignore": "true",
    "data-lpignore": "true",
    "data-bwignore": "true",
    "data-form-type": "other",
} as const;

/** A token, a key, a password: hidden as it is typed, with Show beside it to
 *  check what went in. Only ever what the person typed is shown — a secret
 *  the server holds is never read back into the box.
 *
 *  `masked` is a box still holding the server's stand-in for a stored secret
 *  (`********`): drawn as the plain text it is, with nothing to show. The
 *  input stays the same element either way, so focus survives the switch. */
export function SecretInput({ label, masked = false, className, ...input }: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
    /** What the box is, for the toggle's name: "Show access token". */
    label: string;
    masked?: boolean;
}) {
    const [shown, setShown] = useState(false);
    /* "Show access token", not "Show Access token" — but "Show API key". */
    const first = label.split(" ")[0] ?? "";
    const named = /[A-Z]/.test(first.slice(1)) ? label : label.charAt(0).toLowerCase() + label.slice(1);
    return (
        <span className="ksecret">
            <input
                autoComplete="new-password"
                spellCheck={false}
                {...NOT_A_LOGIN}
                {...input}
                className={"kinput" + (className ? " " + className : "")}
                type={shown || masked ? "text" : "password"}
            />
            {!masked && (
                <button
                    type="button"
                    className="ksecret__show b b--s b--quiet"
                    aria-controls={input.id}
                    aria-label={(shown ? "Hide " : "Show ") + named}
                    disabled={input.disabled}
                    /* The caret stays in the box: the press never blurs it. */
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => setShown((was) => !was)}
                >
                    {shown ? "Hide" : "Show"}
                </button>
            )}
        </span>
    );
}

/** The settings almost nobody needs — an API address, a webhook's check
 *  token, a port — folded under one line at the foot of a form, closed until
 *  somebody opens it. */
export function MoreSettings({ open = false, children }: { open?: boolean; children: ReactNode }) {
    return (
        <details className="kmore" open={open || undefined}>
            <summary>More settings</summary>
            <div className="kmore__body">{children}</div>
        </details>
    );
}

/** The head of one step inside a sheet or a dialog: the way back, small, on
 *  the left; then the service's mark beside what this step is and one plain
 *  line on what doing it does. */
export function StepHead({ onBack, back = "Back", mark, title, line }: {
    onBack: () => void;
    back?: string;
    /** A service's mark, in the kit's circle (`.kmark`). */
    mark?: ReactNode;
    title: string;
    line?: ReactNode;
}) {
    return (
        <header className="kstep__head">
            <button type="button" className="kstep__back" onClick={onBack}>
                <BackIcon size={15} aria-hidden="true" /> {back}
            </button>
            <div className="kstep__title">
                {mark}
                <div>
                    <h3>{title}</h3>
                    {line && <p>{line}</p>}
                </div>
            </div>
        </header>
    );
}
