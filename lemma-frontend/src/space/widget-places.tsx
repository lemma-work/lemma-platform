"use client";

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
