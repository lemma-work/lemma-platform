import { useSyncExternalStore } from "react";

/**
 * What to call the machine Lemma is installed on.
 *
 * Every one of these sentences said "this Mac", in a product that also ships a
 * Windows build and whose CI has a `desktop-windows` job. On Windows the copy
 * read "No coding agents found on this Mac" -- naming hardware the user does
 * not have, on the screen whose entire job is to explain why nothing was found.
 *
 * A function rather than a constant because the answer is only knowable in the
 * browser, and the desktop shell renders these views server-side first. During
 * that first pass there is no `navigator`, so the neutral word is what ships in
 * the HTML and the specific one appears on hydration -- which is correct in
 * both directions: never wrong, and specific as soon as it can be.
 */
export type ComputerNoun = "this Mac" | "this PC" | "this computer";

export function thisComputer(): ComputerNoun {
    if (typeof navigator === "undefined") return "this computer";
    // `userAgentData.platform` is the modern answer and the only one Chromium
    // still populates accurately; `platform` is deprecated but is what Safari
    // and every WKWebView report, which is the case that matters most here.
    const data = (navigator as Navigator & { userAgentData?: { platform?: string } })
        .userAgentData;
    const signal = `${data?.platform ?? ""} ${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`;
    if (/mac/i.test(signal)) return "this Mac";
    if (/win/i.test(signal)) return "this PC";
    return "this computer";
}

/**
 * The same noun with its first letter capitalised, for sentence-initial use.
 *
 * Spelled out rather than done with `slice` at each call site: "this PC"
 * uppercases to "This PC", and a naive `toUpperCase()` on the whole word would
 * give "THIS PC".
 */
export function ThisComputer(): string {
    const noun = thisComputer();
    return noun.charAt(0).toUpperCase() + noun.slice(1);
}

/**
 * The same answer, safe to render.
 *
 * `thisComputer()` returns "this computer" where there is no `navigator` and
 * the real noun where there is -- correct, but *different* between the server
 * render and the first client render, which is a hydration mismatch on every
 * caller. One of them changed an array's length, which React does not repair
 * by patching text: it discards the server subtree and re-renders it.
 *
 * `useSyncExternalStore` is how this repo already handles the same problem in
 * `agent-host-bridge.ts`: the server snapshot is returned for the server render
 * *and* the first client render, and the specific answer arrives in the commit
 * afterwards. So the two renders agree, and the noun still ends up right.
 *
 * The store never changes -- a machine does not stop being a Mac -- so
 * `subscribe` has nothing to do beyond satisfying the signature.
 */
const NEUTRAL: ComputerNoun = "this computer";

/** The server snapshot, exported so a test can assert the two renders agree. */
export const NEUTRAL_FOR_TESTS: ComputerNoun = NEUTRAL;

function subscribe(): () => void {
    return () => {};
}

export function useThisComputer(): ComputerNoun {
    return useSyncExternalStore(subscribe, thisComputer, () => NEUTRAL);
}
