export const VERIFICATION_RESEND_COOLDOWN_MS = 30_000;
const VERIFICATION_EMAIL_SENT_STORAGE_KEY = "lemma.auth.verification-email-sent";

export type VerificationAttempt = "verified" | "invalid" | "rate-limited" | "error";
export type VerificationSendAttempt =
  | "sent"
  | "already-verified"
  | "rate-limited"
  | "error";

type VerificationEmailSentState = {
  userId: string;
  sentAt: number;
};

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

type VerifyResponse = {
  status: "OK" | "EMAIL_VERIFICATION_INVALID_TOKEN_ERROR";
};

type SendResponse = {
  status: "OK" | "EMAIL_ALREADY_VERIFIED_ERROR";
};

export async function runVerificationAttempt(
  verify: () => Promise<VerifyResponse>,
): Promise<VerificationAttempt> {
  try {
    const result = await verify();
    return result.status === "OK" ? "verified" : "invalid";
  } catch (error) {
    return isRateLimitError(error) ? "rate-limited" : "error";
  }
}

export async function runVerificationSend(
  send: () => Promise<SendResponse>,
): Promise<VerificationSendAttempt> {
  try {
    const result = await send();
    return result.status === "OK" ? "sent" : "already-verified";
  } catch (error) {
    return isRateLimitError(error) ? "rate-limited" : "error";
  }
}

export function isRateLimitError(error: unknown): boolean {
  if (error instanceof Response) return error.status === 429;
  if (!(error instanceof Error)) return false;
  const message = error.message.toLowerCase();
  return message.includes("too many") || message.includes("rate limit");
}

export function getVerificationEmailSentAt(
  storage: StorageLike,
  userId: string,
): number | null {
  try {
    const raw = storage.getItem(VERIFICATION_EMAIL_SENT_STORAGE_KEY);
    if (!raw) return null;
    const state = JSON.parse(raw) as Partial<VerificationEmailSentState>;
    return state.userId === userId &&
      typeof state.sentAt === "number" &&
      Number.isFinite(state.sentAt) &&
      state.sentAt > 0
      ? state.sentAt
      : null;
  } catch {
    return null;
  }
}

export function markVerificationEmailSent(
  storage: StorageLike,
  userId: string,
  sentAt: number,
): void {
  try {
    storage.setItem(
      VERIFICATION_EMAIL_SENT_STORAGE_KEY,
      JSON.stringify({ userId, sentAt } satisfies VerificationEmailSentState),
    );
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

export function clearVerificationEmailSent(storage: StorageLike): void {
  try {
    storage.removeItem(VERIFICATION_EMAIL_SENT_STORAGE_KEY);
  } catch {
    // Storage cleanup is best effort; server-side limits remain authoritative.
  }
}

export function verificationTokenFromSearch(search: string): string | null {
  const token = new URLSearchParams(search).get("token")?.trim();
  return token || null;
}

export function verificationCooldownSeconds(
  lastSentAt: number | null,
  now: number,
): number {
  if (lastSentAt === null) return 0;
  return Math.max(0, Math.ceil((lastSentAt + VERIFICATION_RESEND_COOLDOWN_MS - now) / 1000));
}

export function verificationDestination({
  desktopRequestId,
  authLandingUrl,
  redirectUri,
  defaultRedirect,
}: {
  desktopRequestId: string | null;
  authLandingUrl: string;
  redirectUri: string | null;
  defaultRedirect: string;
}): string {
  if (desktopRequestId) {
    const destination = new URL(authLandingUrl);
    destination.searchParams.set("desktop_browser", "1");
    destination.searchParams.set("desktop_request", desktopRequestId);
    return destination.toString();
  }
  return redirectUri || defaultRedirect;
}

export function shouldFetchCurrentUser({
  sessionLoading,
  doesSessionExist,
  hasInvalidClaims,
  desktopRequestId,
  isVerificationExperience,
}: {
  sessionLoading: boolean;
  doesSessionExist: boolean;
  hasInvalidClaims: boolean;
  desktopRequestId: string | null;
  isVerificationExperience: boolean;
}): boolean {
  return (
    !sessionLoading &&
    doesSessionExist &&
    !hasInvalidClaims &&
    !desktopRequestId &&
    !isVerificationExperience
  );
}

export function canCompleteAuthenticatedNavigation({
  sessionLoading,
  doesSessionExist,
  hasInvalidClaims,
  isVerificationExperience,
}: {
  sessionLoading: boolean;
  doesSessionExist: boolean;
  hasInvalidClaims: boolean;
  isVerificationExperience: boolean;
}): boolean {
  return (
    !sessionLoading &&
    doesSessionExist &&
    !hasInvalidClaims &&
    !isVerificationExperience
  );
}

export async function refreshVerifiedSession(
  refreshSession: () => Promise<boolean>,
): Promise<boolean> {
  return refreshSession();
}

export async function signOutForDifferentAccount(
  signOut: () => Promise<void>,
  clearRedirect: () => void,
): Promise<void> {
  await signOut();
  clearRedirect();
}
