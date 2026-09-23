/** A mobile number, as a form holds it and as the API stores it.
 *
 *  `problems()` in `profile-edit.ts` deliberately validates almost nothing —
 *  the server owns the rules, and a form inventing its own refuses things the
 *  API would have taken. This is the exception, and only because it is not an
 *  invention: the shape below is `normalize_mobile_e164` transcribed, digit
 *  bound for digit bound. Verification needs it. "Send a code to this number"
 *  with nothing worth sending to spends one of a handful of attempts an hour
 *  and answers with a 400 a second later, and the button has to know that
 *  before it is pressed rather than after.
 */

/** Slow on purpose. Nobody types a message into WhatsApp in under five
 *  seconds, and the status route is cheap only while it is asked rarely. */
export const VERIFICATION_POLL_MS = 5_000;

/** The exact words the backend's webhook looks for. Not a template to
 *  translate or prettify: `parse_reserved_verification_message` matches the
 *  prefix literally and anything else is an ordinary message to an agent. */
export function verificationMessage(code: string): string {
    return "LEMMA VERIFY " + code.trim();
}

/** What someone typed, as close to E.164 as their keystrokes allow.
 *
 *  The `+` survives only if they wrote one. A number without a country code is
 *  worthless and we never guess which country anybody is in, so a bare
 *  `4155552671` stays incomplete rather than quietly becoming American. */
export function normalizeMobileNumber(value: string): string {
    const trimmed = value.trim();
    if (!trimmed) return "";
    return (trimmed.startsWith("+") ? "+" : "") + trimmed.replace(/\D/g, "");
}

/** A number the API already holds, as E.164.
 *
 *  Stored numbers passed the server's validator on the way in, so the country
 *  code is there whether or not the `+` survived the round trip. */
export function storedMobileNumber(value: string): string {
    const digits = value.replace(/\D/g, "");
    return digits ? "+" + digits : "";
}

/** Whether a number is complete enough to send anywhere. Empty is not. */
export function isCompleteMobileNumber(value: string): boolean {
    return /^\+[1-9]\d{7,14}$/.test(value);
}
