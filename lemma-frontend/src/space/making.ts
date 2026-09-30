import { useCallback, useState } from "react";
import { isForbidden } from "@/session/auth-state";

/** Why making something failed, in words for the person who pressed the button. */
export function sayWhyNotMade(error: unknown): string {
    if (isForbidden(error)) return "You can read this space but not add to it. Ask an admin to make you an editor.";
    if (error instanceof Error && error.message) return error.message;
    return "Something went wrong. Try again.";
}

/** A button that makes something: busy while it works, and a reason when it
 *  does not. A failure here used to be swallowed, which from the outside is
 *  indistinguishable from a button that does nothing. */
export function useMaking() {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const run = useCallback(async (what: string, work: () => Promise<void>) => {
        if (busy) return;
        setBusy(what);
        setError(null);
        try {
            await work();
        } catch (problem) {
            setError(sayWhyNotMade(problem));
        } finally {
            setBusy(null);
        }
    }, [busy]);
    return { busy, error, run, clear: () => setError(null) };
}
