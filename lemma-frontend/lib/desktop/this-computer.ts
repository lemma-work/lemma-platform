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
