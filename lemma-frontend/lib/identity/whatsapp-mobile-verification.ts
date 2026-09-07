export const WHATSAPP_VERIFICATION_POLL_INTERVAL_MS = 5_000;

export function buildWhatsAppVerificationMessage(code: string): string {
  return `LEMMA VERIFY ${code.trim()}`;
}

/**
 * What someone typed, as close to E.164 as their keystrokes allow.
 *
 * The `+` is kept only when they wrote one: a number is worthless without a
 * country code and we never guess which country someone is in, so a bare
 * `4155552671` has to stay incomplete rather than silently become a US number.
 */
export function normalizeMobileNumber(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const digits = trimmed.replace(/\D/g, "");
  return `${trimmed.startsWith("+") ? "+" : ""}${digits}`;
}

/**
 * A number the API already holds, as E.164.
 *
 * Stored numbers passed validation on the way in, so the country code is there
 * whether or not the `+` survived the round trip.
 */
export function normalizeStoredMobileNumber(value: string): string {
  const digits = value.replace(/\D/g, "");
  return digits ? `+${digits}` : "";
}

/** Whether a number is complete enough to send anywhere. Empty is not. */
export function isCompleteMobileNumber(value: string): boolean {
  return /^\+[1-9]\d{7,14}$/.test(value);
}
