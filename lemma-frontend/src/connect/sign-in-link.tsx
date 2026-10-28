"use client";

import { useState } from "react";
import { copyText } from "@/desktop/clipboard";
import { CopyIcon, ExternalIcon } from "@/ui/icons";
import { consentLabel, consentNote } from "./consent";
import { noteRoundTrip } from "./round-trip";

/** The sign-in address, as something the person can act on by hand.
 *
 *  Opening the provider's tab is not something this page can count on: an
 *  in-app browser (a chat app's webview, say) either refuses `window.open` or
 *  hands back a window that never navigates, and both look like a Connect
 *  button that did nothing. So once there is an address, it is always shown
 *  too — a real link, which such browsers do follow, and a copy for finishing
 *  in another browser altogether. `opened` is whether a tab was opened for it
 *  already, or null when none was tried yet; it only changes what the line
 *  says. */
export function SignInLink({ url, app, opened }: { url: string; app: string; opened: boolean | null }) {
    const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
    const note = consentNote(url, app);
    return (
        <span className="signinlink">
            <span className="signinlink__acts">
                <a className="linkish" href={url} target="_blank" rel="noopener" onClick={noteRoundTrip}>
                    {consentLabel(url, app)} <ExternalIcon size={12} />
                </a>
                <button type="button" className="linkish"
                    onClick={() => copyText(url).then(() => { noteRoundTrip(); setCopy("copied"); }, () => setCopy("failed"))}>
                    <CopyIcon size={12} /> {copy === "copied" ? "Link copied" : "Copy link"}
                </button>
            </span>
            {copy === "failed" && (
                /* Nothing could reach the clipboard: the address itself, ready
                   to be selected and copied by hand. */
                <input className="signinlink__url" readOnly value={url} aria-label="Sign-in link" onFocus={(event) => event.currentTarget.select()} />
            )}
            <small>
                {opened === null
                    ? "Or copy the link into another browser to finish there."
                    : opened
                        ? "Nothing opened? Use the link, or copy it into another browser to finish there."
                        : "This browser did not open a new tab. Use the link, or copy it into another browser to finish there."}
            </small>
            {note && <small>{note}</small>}
        </span>
    );
}
