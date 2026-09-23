import type { ReactNode } from "react";
import { SharedTheme } from "./shared-theme";

/** Drawn here rather than imported. The icon set is a client package that
 *  reaches for `createContext`, and this shell is a server component — but
 *  more usefully, this page is loaded by strangers who should not have to
 *  fetch an icon library to read one document. */
function DownloadMark() {
    return (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M8 2v8m0 0 3-3m-3 3L5 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M2.5 11v1.5A1.5 1.5 0 0 0 4 14h8a1.5 1.5 0 0 0 1.5-1.5V11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
    );
}

/** The frame around a shared document.
 *
 *  Deliberately plain: the reader has no account, no pod and no rail, so there
 *  is nothing to navigate — and anything shaped like navigation would be a
 *  door they cannot open.
 *
 *  The filename sits in the bar rather than above the content, because a
 *  document usually opens with a heading of its own and two titles stacked
 *  reads as a mistake. The bar is also where Download belongs: it is the one
 *  thing a reader might want that the page cannot do for them. */
export function SharedShell({
    name,
    title,
    note,
    download,
    wide,
    bleed,
    foot,
    children,
}: {
    name?: string;
    title?: string;
    note?: string;
    download?: string;
    wide?: boolean;
    /** For a document that is already a page — an HTML file, a PDF, a
     *  picture. A card with a margin around one of those is a frame around a
     *  frame, and the reader spends the screen they have on padding. */
    bleed?: boolean;
    /** Anything the reader should know that has nowhere else to sit once the
     *  content has taken the viewport. */
    foot?: ReactNode;
    children?: ReactNode;
}) {
    return (
        <div className={"shared" + (bleed ? " shared--bleed" : "")}>
            <header className="shared__bar">
                {/* The real mark, written out rather than imported: the icon
                    module pulls a client package, and this shell is a server
                    component. The classes are the app's own, so the two stay
                    the same logo without sharing a component. */}
                <a className="lemma-logo shared__home" href="/" aria-label="Lemma">
                    <span className="lemma-brand-mark" aria-hidden="true"><i /><i /><i /></span>
                    <span className="lemma-wordmark">Lemma</span>
                </a>
                {name && <span className="shared__rule" aria-hidden="true" />}
                {name && <span className="shared__name" title={name}>{name}</span>}
                <span className="shared__spacer" />
                <SharedTheme />
                {download && (
                    <a className="shared__get" href={download} title={"Download " + (name ?? "this file")}>
                        <DownloadMark /><span>Download</span>
                    </a>
                )}
            </header>

            {bleed ? (
                <main className="shared__bleed">{children}</main>
            ) : (
                <main className={"shared__sheet" + (wide ? " shared__sheet--wide" : "")}>
                    {title && <h1>{title}</h1>}
                    {note && <p className="shared__note">{note}</p>}
                    {children}
                </main>
            )}

            <footer className="shared__foot">
                <span>Shared from Lemma with a link that expires.</span>
                {foot}
            </footer>
        </div>
    );
}
