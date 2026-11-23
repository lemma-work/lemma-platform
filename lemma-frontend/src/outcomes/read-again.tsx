"use client";

import { Icon } from "./parts";

/** The way forward from a read that failed (design.md, "States"): read it
 *  again, said "Try again", and "Trying…" while it is. */
export function TryAgain({ busy = false, onClick, size = "m" }: { busy?: boolean; onClick: () => void; size?: "s" | "m" }) {
    return (
        <button type="button" className={`b b--${size} ${size === "m" ? "b--primary" : "b--secondary"}`} disabled={busy} onClick={onClick}>
            {size === "m" && <Icon name="refresh" size={15} />}
            {busy ? "Trying…" : "Try again"}
        </button>
    );
}

/** The status code of a failed read, said small under it where there is
 *  one: "Error 503". */
export function codeOf(error: unknown): string | undefined {
    if (!error || typeof error !== "object") return undefined;
    const candidate = error as { statusCode?: unknown; status?: unknown };
    const status = candidate.statusCode ?? candidate.status;
    return typeof status === "number" && status > 0 ? `Error ${status}` : undefined;
}
