import { formatRetryDelay } from "@/components/auth/portal/auth/auth-errors";
import { buildApiUrl } from "@/components/auth/portal/auth/config";

export type Challenge = { challenge_id: string; expires_at: string };

export type ThirdPartyId = "google" | "active-directory";

export type ContinueMethod =
  | { method: "password" }
  | { method: "thirdparty"; provider: ThirdPartyId }
  | ({ method: "code" } & Challenge);

export type EmailCodePath =
  | "browser"
  | "continue"
  | "start"
  | "resend"
  | "verify";

export const GENERIC_FAILURE = "Unable to continue. Please try again.";

type Fetcher = typeof fetch;

export class EmailCodeError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    message: string,
    status: number,
    code: string | null = null,
    retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "EmailCodeError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function retryAfterSeconds(response: Response): number | null {
  const value = response.headers?.get?.("retry-after");
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds > 0) return Math.ceil(seconds);
  const date = Date.parse(value);
  if (!Number.isFinite(date)) return null;
  return Math.max(1, Math.ceil((date - Date.now()) / 1000));
}

/**
 * The server's own words, from whichever envelope this response came in.
 *
 * `message` first, because that is what the API actually sends: every response
 * from the main app goes through the unified `{message, code, request_id,
 * details}` envelope in `core.api.exception_handlers`. Reading only `detail`
 * — which is what this code did before it was shared — meant *every* message
 * this flow can produce was dropped on the floor and shown as the generic
 * failure: "The code did not match; try again", "Code expired or attempts
 * exhausted", "Wait sixty seconds before requesting another code". People were
 * told "Unable to continue" and given nothing to act on.
 *
 * `detail` stays as the fallback because the SuperTokens app is mounted
 * separately at `/st` and does not share those handlers, and because a proxy in
 * front of the API can answer an outage in something else entirely.
 */
function serverError(
  body: unknown,
): { message: string | null; code: string | null } {
  if (typeof body !== "object" || body === null) {
    return { message: null, code: null };
  }
  const payload = body as { message?: unknown; detail?: unknown; code?: unknown };
  const code =
    typeof payload.code === "string" && payload.code.trim()
      ? payload.code.trim()
      : null;
  for (const candidate of [payload.message, payload.detail]) {
    if (typeof candidate === "string" && candidate.trim()) {
      return { message: candidate, code };
    }
    if (typeof candidate === "object" && candidate !== null) {
      const detail = candidate as { code?: unknown; message?: unknown };
      const nestedCode =
        typeof detail.code === "string" && detail.code.trim()
          ? detail.code.trim()
          : code;
      if (typeof detail.message === "string" && detail.message.trim()) {
        return { message: detail.message, code: nestedCode };
      }
    }
  }
  return { message: null, code };
}

export async function emailCodeRequest<T>(
  path: EmailCodePath,
  body: object,
  fetcher: Fetcher = fetch,
): Promise<T> {
  // A transport failure is deliberately left to propagate as itself. Wrapping it
  // would replace a browser's own diagnosis ("Failed to fetch", a DNS message,
  // a blocked-by-extension message) with a guess, and `emailCodeErrorMessage`
  // already shows whatever it carries.
  const response = await fetcher(buildApiUrl(`/auth/email-code/${path}`), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "st-auth-mode": "cookie" },
    body: JSON.stringify(body),
  });
  // A proxy in front of the API answers an outage in HTML, and a body that does
  // not parse would otherwise reach the person as a JSON syntax error.
  const result: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const retry = retryAfterSeconds(response);
    const parsed = serverError(result);
    const base = parsed.message ?? GENERIC_FAILURE;
    const message =
      retry === null
        ? base
        : `${base.replace(/\.$/, "")}. Try again in ${formatRetryDelay(retry)}.`;
    throw new EmailCodeError(message, response.status, parsed.code, retry);
  }
  return result as T;
}

/**
 * The browser's half of the binding.
 *
 * Minted once and kept for the whole exchange, including across "change email":
 * the cookie the API sets alongside it is what every later call is compared
 * against, so a second nonce would strand the first.
 */
export async function mintNonce(fetcher: Fetcher = fetch): Promise<string> {
  const { nonce } = await emailCodeRequest<{ nonce: string }>(
    "browser",
    {},
    fetcher,
  );
  return nonce;
}

export async function continueWithEmail(
  input: { email: string; nonce: string; abandonChallengeId?: string | null },
  fetcher: Fetcher = fetch,
): Promise<ContinueMethod> {
  return emailCodeRequest<ContinueMethod>(
    "continue",
    {
      email: input.email,
      nonce: input.nonce,
      // Omitted rather than sent as null when there is nothing to retire, so
      // the request body says only what is true.
      ...(input.abandonChallengeId
        ? { abandon_challenge_id: input.abandonChallengeId }
        : {}),
    },
    fetcher,
  );
}

export async function startChallenge(
  input: { email: string; nonce: string },
  fetcher: Fetcher = fetch,
): Promise<Challenge> {
  return emailCodeRequest<Challenge>("start", input, fetcher);
}

export async function resendChallenge(
  input: { nonce: string; challengeId: string },
  fetcher: Fetcher = fetch,
): Promise<Challenge> {
  return emailCodeRequest<Challenge>(
    "resend",
    { nonce: input.nonce, challenge_id: input.challengeId },
    fetcher,
  );
}

export async function verifyChallenge(
  input: { nonce: string; challengeId: string; code: string },
  fetcher: Fetcher = fetch,
): Promise<void> {
  await emailCodeRequest<{ status: string }>(
    "verify",
    { nonce: input.nonce, challenge_id: input.challengeId, code: input.code },
    fetcher,
  );
}

export function emailCodeErrorMessage(
  cause: unknown,
  fallback: string = GENERIC_FAILURE,
): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}
