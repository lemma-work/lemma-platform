export const VERIFICATION_RESEND_COOLDOWN_MS = 30_000;

export type VerificationAttempt = "verified" | "invalid" | "error";
export type VerificationSendAttempt = "sent" | "already-verified" | "error";

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
  } catch {
    return "error";
  }
}

export async function runVerificationSend(
  send: () => Promise<SendResponse>,
): Promise<VerificationSendAttempt> {
  try {
    const result = await send();
    return result.status === "OK" ? "sent" : "already-verified";
  } catch {
    return "error";
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
