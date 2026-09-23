/** Names, as a person sees them.
 *
 *  What a pod's default responder is called, wherever a person can see it.
 *
 *  The platform settled this: `pod_default` and `POD_DEFAULT` are wire values
 *  and stay as they are, but the thing answering you is called **Lem**. Run
 *  `humanizeName` over the row name instead and you get "Pod Default", which
 *  is the job title the product deliberately does not use.
 *
 *  Keeping the same two rules here means the two frontends cannot disagree
 *  about who is talking. */

const POD_DEFAULT_ROW_NAME = "pod_default";
const POD_DEFAULT_SELECTOR = "POD_DEFAULT";

export const DEFAULT_RESPONDER_NAME = "Lem";

export function isPodDefaultAgent(name: string | null | undefined, kind?: string | null): boolean {
    return name === POD_DEFAULT_ROW_NAME || name === POD_DEFAULT_SELECTOR || kind === POD_DEFAULT_SELECTOR;
}

/** "customer-support_bot" → "Customer support bot". Display only — never for
 *  an href, an API call, or anywhere the raw name is the identifier. */
export function humanizeAgentName(name: string): string {
    const spaced = name.replace(/[-_]+/g, " ").trim();
    if (!spaced) return name;
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function displayAgentName(name: string, kind?: string | null): string {
    if (isPodDefaultAgent(name, kind)) return DEFAULT_RESPONDER_NAME;
    return humanizeAgentName(name);
}

/** Two letters for a face with no picture.
 *
 *  Splits on the punctuation names actually arrive with — spaces, dots,
 *  underscores, hyphens — and drops an email's domain first, so
 *  `priya.shah@acme.com` is PS rather than PR. Lives here rather than in a
 *  source because a rename has to compute the same two letters the source
 *  would have, and two versions of this would have drifted the moment one
 *  was fixed.
 */
export function initialsOf(name: string): string {
    const parts = name.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
    const letters = parts.slice(0, 2).map((word) => word[0] ?? "");
    return (letters.join("") || name.slice(0, 2)).toUpperCase();
}
