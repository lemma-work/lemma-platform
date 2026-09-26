/** What went wrong, said to the person it happened to.
 *
 *  Auth is the one place where the server's own words are least usable. It
 *  answers `{"status":"WRONG_CREDENTIALS_ERROR"}` and `"Too many
 *  authentication attempts"`, and it answers 429 with a `retry-after` header
 *  that nobody reads. A screen that prints those is asking somebody locked out
 *  of their account to interpret an API.
 *
 *  Pure, and every branch returns a sentence somebody can act on — which for a
 *  rate limit means saying *how long*, because "try again later" and a closed
 *  tab are the same outcome.
 */

/** Which request failed. The label differs because "too many attempts" means
 *  something different when it is a password than when it is an email check. */
export type Attempt = "sign-in" | "sign-up" | "reset" | "new-password" | "verify";

const LOCKED: Record<Attempt, string> = {
    "sign-in": "Too many sign-in attempts",
    "sign-up": "Too many attempts to make an account",
    reset: "Too many password reset requests",
    "new-password": "Too many attempts to set a password",
    verify: "Too many verification attempts",
};

/** How long the server said to wait, in seconds, or null.
 *
 *  `retry-after` is either a count of seconds or an HTTP date, and both are
 *  in the wild. A date in the past reads as one second rather than as a
 *  negative number somebody would be shown. */
export function retryAfterSeconds(header: string | null, now: number = Date.now()): number | null {
    if (!header) return null;
    const trimmed = header.trim();
    if (!trimmed) return null;

    const seconds = Number(trimmed);
    if (Number.isFinite(seconds) && seconds > 0) return Math.ceil(seconds);

    const at = Date.parse(trimmed);
    if (!Number.isFinite(at)) return null;
    return Math.max(1, Math.ceil((at - now) / 1000));
}

/** A wait, in words. Minutes once it is longer than a minute, because "ninety
 *  seconds" is a number somebody has to convert and "2 minutes" is not. */
export function sayDelay(seconds: number): string {
    if (seconds < 60) return seconds + (seconds === 1 ? " second" : " seconds");
    const minutes = Math.ceil(seconds / 60);
    return minutes + (minutes === 1 ? " minute" : " minutes");
}

/** What the server's refusal means here.
 *
 *  Takes the pieces rather than a `Response`, so the decision is testable
 *  without a fetch: a status, whatever `retry-after` said, and whatever the
 *  body said about itself.
 */
export function authFailure(
    attempt: Attempt,
    status: number,
    retryAfter: string | null = null,
    said = "",
    now: number = Date.now(),
): string {
    const lowered = said.trim().toLowerCase();

    /* The rate limit arrives both ways — as a 429, and as a 200-shaped refusal
       carrying this sentence — so both are read. */
    if (status === 429 || lowered === "too many authentication attempts") {
        const wait = retryAfterSeconds(retryAfter, now);
        return LOCKED[attempt] + "." + (wait
            ? " Try again in " + sayDelay(wait) + "."
            : " Wait a little and try again.");
    }

    /* The proof-of-work expired, which is not the person's fault and not
       something they can fix by changing what they typed. */
    if (lowered.includes("proof-of-work") || lowered.includes("security check")) {
        return "The security check expired before that went through. Try again.";
    }

    if (status >= 500) return "Signing in is temporarily unavailable. Try again shortly.";

    return "That could not be completed. Try again.";
}

/** A thrown thing, as a sentence.
 *
 *  SuperTokens raises the server's own `GENERAL_ERROR` message as an `Error`,
 *  so without this the proof-of-work failing reaches the screen as the literal
 *  words **"Missing proof-of-work"** — which is true, is the API's sentence
 *  rather than a person's, and names something the reader did not do and
 *  cannot do. Verified against the live API: that endpoint answers exactly
 *  that, 400, when the header is absent.
 *
 *  Only phrases we have actually seen are translated. Anything else is passed
 *  through, because a server that has gone to the trouble of explaining itself
 *  usually knows something this app does not.
 */
export function sayProblem(error: unknown): string {
    if (!(error instanceof Error) || !error.message) {
        return "Lemma could not be reached. Check your connection and try again.";
    }
    const lowered = error.message.toLowerCase();
    if (lowered.includes("proof-of-work") || lowered.includes("security check")) {
        return "The security check expired before that went through. Try again.";
    }
    return error.message;
}

/** Whether a sign-up's email complaint is "this address already has an account".
 *
 *  SuperTokens answers `EMAIL_ALREADY_EXISTS_ERROR` as a `FIELD_ERROR` on the
 *  email input carrying its own sentence ("This email already exists. Please
 *  sign in instead.") rather than a status, and the backend answers an
 *  existing password account that way whatever the signup mode. Recognised so
 *  the screen can offer the sign-in itself instead of printing the recipe's
 *  words against an address that is not wrong. */
export function isExistingAccount(emailComplaint: string | undefined): boolean {
    return (emailComplaint ?? "").toLowerCase().includes("already exists");
}
