"use client";

import type { WebWidget } from "@/data/contacts";
import { Modal } from "@/shell/modal";
import { CopyButton } from "@/thread/copy-button";

/** Where something lives on the web: a link to share, and code for a website. */
export function WidgetPlaces({ link, embed, linkLabel = "Share a link" }: { link: string; embed: string; linkLabel?: string }) {
    return (
        <div className="cplaces">
            <div className="cplace">
                <span className="smanage__label">{linkLabel}</span>
                <div className="cwidget__embed">
                    <code>{link}</code>
                    <CopyButton text={link} label="Copy the link" />
                </div>
                <a className="linkish" href={link} target="_blank" rel="noopener noreferrer">Open the page</a>
            </div>
            <div className="cplace">
                <span className="smanage__label">Or put it on your website</span>
                <div className="cwidget__embed">
                    <code>{embed}</code>
                    <CopyButton text={embed} label="Copy the code" />
                </div>
            </div>
        </div>
    );
}

/** A chat's signing secret, the one time it is shown.
 *
 *  Sticky: a click beside it or a stray Escape would lose the secret for good,
 *  so only saying it has been copied closes it. */
export function SecretSheet({ widget, secret, onClose }: { widget: WebWidget; secret: string; onClose: () => void }) {
    return (
        <Modal title={widget.name} subtitle="Copy the signing secret now" sticky onClose={onClose}>
            <div className="csheet">
                <p>
                    Your site’s server uses this to sign in its own customers, so they are answered as contacts. It is shown only now; keep it off web pages.
                </p>
                <div className="cwidget__embed">
                    <code>{secret}</code>
                    <CopyButton text={secret} label="Copy the secret" />
                </div>
                <p>Then put this on the page:</p>
                <div className="cwidget__embed">
                    <code>{widget.embed}</code>
                    <CopyButton text={widget.embed} label="Copy the code" />
                </div>
                <div className="csheet__actions">
                    <button type="button" className="pill-button" onClick={onClose}>I’ve copied the secret</button>
                </div>
            </div>
        </Modal>
    );
}
