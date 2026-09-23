/** The proof-of-work the API asks for before it will take an auth request.
 *
 *  Altcha is a hashcash: the server hands out a salt, a signature and a
 *  ceiling, and the browser finds the one number under that ceiling whose
 *  `SHA-256(salt + number)` matches the challenge. It costs a visitor a moment
 *  and costs somebody spraying an endpoint their whole budget.
 *
 *  **It is not optional when it is on.** `auth_abuse.verify_altcha` refuses a
 *  request with no proof — "Missing proof-of-work" — so a portal that skipped
 *  this would not degrade, it would simply never sign anybody in on a
 *  deployment that had it enabled. Whether it is on is the server's to say and
 *  is asked at `/auth/altcha/challenge`, which answers `{enabled:false}` when
 *  it is not.
 *
 *  Note the endpoint is on Lemma's own `/auth`, not under `/st` — it is not
 *  SuperTokens, it guards SuperTokens.
 */

import { onApi } from "./config";

export type Purpose = "signup" | "verification" | "password-reset" | "signin-risk";

export interface Challenge {
    enabled: boolean;
    algorithm?: string;
    challenge?: string;
    maxnumber?: number;
    salt?: string;
    signature?: string;
}

function hex(bytes: ArrayBuffer): string {
    return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The answer to one challenge, encoded the way the API reads it back.
 *
 *  Pure apart from `crypto.subtle`, which node has too — so the search is
 *  tested rather than taken on trust. Base64url, because it travels as a
 *  header value and `+` and `/` do not survive that reliably.
 */
export async function solve(challenge: Challenge): Promise<string | null> {
    if (!challenge.enabled) return null;
    const { algorithm, salt, signature, maxnumber } = challenge;
    if (algorithm !== "SHA-256" || !challenge.challenge || !salt || !signature || maxnumber === undefined) {
        throw new Error("The security check arrived in a form this app cannot answer.");
    }

    const encoder = new TextEncoder();
    let number = 0;
    for (; number <= maxnumber; number += 1) {
        const digest = await crypto.subtle.digest("SHA-256", encoder.encode(salt + number));
        if (hex(digest) === challenge.challenge) break;
        /* Yield periodically. The ceiling is high enough that a slow machine
           would otherwise lock its own tab solid while somebody watches a
           button do nothing. */
        if (number % 1000 === 0) await new Promise((r) => setTimeout(r, 0));
    }
    if (number > maxnumber) throw new Error("The security check could not be completed.");

    const answer = JSON.stringify({ algorithm, challenge: challenge.challenge, number, salt, signature });
    return btoa(answer).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Ask for a challenge, answer it, and hand back the header to send with it.
 *
 *  An empty object when the server says it is off, so the caller spreads it
 *  either way and never branches on whether protection is enabled. */
export async function proofHeader(purpose: Purpose, fetcher: typeof fetch = fetch): Promise<Record<string, string>> {
    const url = onApi("/auth/altcha/challenge?purpose=" + encodeURIComponent(purpose));
    if (!url) return {};

    const response = await fetcher(url, { credentials: "include" });
    if (!response.ok) throw new Error("The security check is temporarily unavailable. Try again shortly.");

    const challenge = (await response.json()) as Challenge;
    const proof = await solve(challenge);
    return proof ? { "x-altcha-payload": proof } : {};
}
