"use client";

import { useState, type FormEvent } from "react";
import { publicSiteUrl } from "@/site/seo/site-url";
import { sharedLinkPath } from "./model";

/** A link to a Lemma or a plugin someone shared, opened where this app
 *  installs it: what it brings and needs is shown before anything is added. */
export function SharedLink() {
    const [link, setLink] = useState("");
    const [wrong, setWrong] = useState(false);
    const open = (event: FormEvent) => {
        event.preventDefault();
        const path = sharedLinkPath(link, [window.location.origin, new URL(publicSiteUrl()).origin]);
        /* Only ever one of this app's own paths: one slash, then a word. */
        const own = path !== null && path.startsWith("/") && !path.startsWith("//");
        setWrong(!own);
        if (own) window.location.assign(path);
    };
    return (
        <section className="ox-shelf__shared">
            <form className="ox-own" onSubmit={open}>
                <label htmlFor="shared-link">Link</label>
                <input id="shared-link" value={link} onChange={(event) => { setLink(event.target.value); setWrong(false); }} placeholder="A GitHub repository, or a link someone shared from Lemma" />
                <button type="submit" className="ox-btn" disabled={!link.trim()}>Look</button>
            </form>
            <p className="ox-vs" role={wrong ? "alert" : undefined}>{wrong ? "That isn’t a link this can open. Paste a GitHub repository or a Lemma share link." : "You see what it brings and what it needs before anything is added."}</p>
        </section>
    );
}
