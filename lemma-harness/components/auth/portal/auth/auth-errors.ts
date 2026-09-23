import STGeneralError from "supertokens-web-js/utils/error";

export type AuthRequestKind =
  | "sign-in"
  | "sign-up"
  | "password-reset"
  | "password-change"
  | "email-check";

type AuthErrorPayload = {
  status?: string;
  message?: string;
};

function retryDelaySeconds(response: Response): number | null {
  const value = response.headers.get("retry-after");
  if (!value) return null;

  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds > 0) return Math.ceil(seconds);

  const date = Date.parse(value);
  if (!Number.isFinite(date)) return null;
  return Math.max(1, Math.ceil((date - Date.now()) / 1000));
}

export function formatRetryDelay(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} second${seconds === 1 ? "" : "s"}`;
  }
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

function rateLimitLabel(kind: AuthRequestKind): string {
  switch (kind) {
    case "sign-in":
      return "Too many sign-in attempts";
    case "sign-up":
      return "Too many account creation attempts";
    case "password-reset":
      return "Too many password reset requests";
    case "password-change":
      return "Too many password change attempts";
    case "email-check":
      return "Too many email checks";
  }
}

async function responsePayload(response: Response): Promise<AuthErrorPayload> {
  try {
    return (await response.clone().json()) as AuthErrorPayload;
  } catch {
    return {};
  }
}

export async function authErrorMessageFromResponse(
  response: Response,
  kind: AuthRequestKind,
): Promise<string> {
  const payload = await responsePayload(response);
  const serverMessage = payload.message?.trim().toLowerCase() || "";

  if (
    response.status === 429 ||
    serverMessage === "too many authentication attempts"
  ) {
    const delay = retryDelaySeconds(response);
    const suffix = delay
      ? ` Please try again in ${formatRetryDelay(delay)}.`
      : " Please wait a little while and try again.";
    return `${rateLimitLabel(kind)}.${suffix}`;
  }

  if (
    serverMessage.includes("proof-of-work") ||
    serverMessage.includes("security check")
  ) {
    return "The private security check expired or could not be completed. Please try again.";
  }

  if (response.status >= 500) {
    return "Authentication is temporarily unavailable. Please try again shortly.";
  }

  return "We couldn’t complete that authentication request. Please try again.";
}

export async function runAuthRequest<T>(
  kind: AuthRequestKind,
  request: () => Promise<T>,
): Promise<T> {
  try {
    return await request();
  } catch (error) {
    if (STGeneralError.isThisError(error)) throw error;
    if (error instanceof Response) {
      throw new STGeneralError(await authErrorMessageFromResponse(error, kind));
    }
    if (
      error instanceof Error &&
      (error.message.includes("proof-of-work") ||
        error.message.includes("Authentication protection"))
    ) {
      throw new STGeneralError(
        "The private security check is temporarily unavailable. Please try again.",
      );
    }
    throw new STGeneralError(
      "We couldn’t reach Lemma. Check your connection and try again.",
    );
  }
}
