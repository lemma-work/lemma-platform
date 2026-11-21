"use client";

import { Glyph } from "./add-connector";

/** A service's mark in the kit's circle: its logo where the catalogue has
 *  one, the kind's glyph for a server, database or API the organization
 *  added (its catalogue logo is the same for every one of them, and says
 *  nothing about which), and the first letter of its name otherwise. */
export function ServiceMark({ entry, kind = null, large = false }: {
    entry: { title: string; icon?: string | null };
    kind?: string | null;
    large?: boolean;
}) {
    return (
        <span className={"kmark" + (large ? " kmark--lg" : "")} aria-hidden="true">
            {kind ? <Glyph kind={kind} size={large ? 26 : 20} />
                : entry.icon ? <img src={entry.icon} alt="" />
                    : <span className="kconn-initial">{entry.title.slice(0, 1)}</span>}
        </span>
    );
}
