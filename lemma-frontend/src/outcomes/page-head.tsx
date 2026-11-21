"use client";

import type { ReactNode } from "react";

/** A page's header, the same on every page that is a place of its own
 *  (kit `.ph`): its title, one line under it, and the page's buttons at the
 *  right, level with the two. No counts in the title: what a count says
 *  goes in the line, or on the page's filters. */
export function PageHead({ title, line, children }: { title: string; line?: ReactNode; children?: ReactNode }) {
    return (
        <header className="ph">
            <div className="ph__row">
                <div className="ph__words">
                    <h1 className="ph__t">{title}</h1>
                    {line && <p className="ph__line">{line}</p>}
                </div>
                {children && <div className="ph__acts">{children}</div>}
            </div>
        </header>
    );
}
