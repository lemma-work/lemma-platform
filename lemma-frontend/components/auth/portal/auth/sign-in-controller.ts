import type {
  Challenge,
  ContinueMethod,
  ThirdPartyId,
} from "@/components/auth/portal/auth/email-code-client";
import type { ThirdPartyProvider } from "@/components/auth/portal/auth/supertokens";

export type AuthMode = "signin" | "signup";

export type SignInStep =
  | "identifier"
  | "resolving"
  | "password"
  | "authenticating"
  | "handoff"
  | "code";

export type SignInState = {
  step: SignInStep;
  email: string;
  /** Minted once and kept for the whole exchange; see `mintNonce`. */
  nonce: string | null;
  challenge: Challenge | null;
  provider: ThirdPartyProvider | null;
  /**
   * A challenge this browser walked away from, still owed a cancellation.
   *
   * Cleared only when a `/continue` succeeds, never merely when the step
   * changes: the cooldown it exists to clear is held against the binding, so a
   * second detour would arrive with nothing left to retire and be refused.
   */
  abandonChallengeId: string | null;
  error: string | null;
  /** Whether to offer "Email me a code instead" beside the password field. */
  passwordFallback: boolean;
};

export const initialSignInState: SignInState = {
  step: "identifier",
  email: "",
  nonce: null,
  challenge: null,
  provider: null,
  abandonChallengeId: null,
  error: null,
  passwordFallback: false,
};

export function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

export function validateIdentifier(raw: string): string | null {
  return normalizeEmail(raw) ? null : "Enter your email address.";
}

export function beginResolve(state: SignInState, email: string): SignInState {
  return {
    ...state,
    step: "resolving",
    email: normalizeEmail(email),
    error: null,
    passwordFallback: false,
  };
}

export function continueRequest(
  state: SignInState,
  email: string,
  nonce: string,
): { email: string; nonce: string; abandonChallengeId: string | null } {
  return {
    email: normalizeEmail(email),
    nonce,
    abandonChallengeId: state.abandonChallengeId,
  };
}

export function applyContinueResult(
  state: SignInState,
  nonce: string,
  result: ContinueMethod,
  availableProviders: readonly ThirdPartyProvider[],
): SignInState {
  const settled = { ...state, nonce, error: null, abandonChallengeId: null };
  if (result.method === "password") {
    return { ...settled, step: "password", challenge: null, provider: null };
  }
  if (result.method === "thirdparty") {
    const provider = availableProviders.find(
      (candidate) => candidate.id === result.provider,
    );
    if (!provider) {
      // The account really does sign in with this provider, and this build
      // cannot offer it -- inside the Telegram mini app, or if it were ever
      // dropped from the deployment's list.
      //
      // The message used to end "you can send yourself a code instead", which
      // was not true twice over: nothing on this step renders that offer, and
      // a code could not be honoured anyway. `complete_verified_account` would
      // mint a session for the third-party account on mailbox proof alone,
      // which is exactly what `override_thirdparty` refuses to do. So this
      // says the one thing that does work, and claims nothing else.
      return {
        ...state,
        step: "identifier",
        nonce,
        error: `This email signs in with ${labelFor(result.provider)}, which isn't available here. Open Lemma in a browser to continue with ${labelFor(result.provider)}.`,
        passwordFallback: false,
      };
    }
    return { ...settled, step: "handoff", provider, challenge: null };
  }
  return {
    ...settled,
    step: "code",
    provider: null,
    challenge: { challenge_id: result.challenge_id, expires_at: result.expires_at },
  };
}

function labelFor(provider: ThirdPartyId): string {
  return provider === "active-directory" ? "Microsoft" : "Google";
}

/**
 * Hand off to a provider the person picked themselves.
 *
 * Through the same state the `/continue` answer uses, rather than calling the
 * redirect from the button: a `redirectToProvider` that answers ERROR, or
 * throws, is otherwise swallowed by a fire-and-forget `void` and the button
 * simply looks dead.
 */
export function beginHandoff(
  state: SignInState,
  provider: ThirdPartyProvider,
): SignInState {
  return { ...state, step: "handoff", provider, error: null };
}

export function failResolve(state: SignInState, message: string): SignInState {
  return { ...state, step: "identifier", error: message };
}

export function beginPasswordSubmit(state: SignInState): SignInState {
  return { ...state, step: "authenticating", error: null };
}

export function applyPasswordRejection(
  state: SignInState,
  rejection: { message: string; fallback: boolean },
): SignInState {
  return {
    ...state,
    step: "password",
    error: rejection.message,
    passwordFallback: rejection.fallback,
  };
}

export function changeEmail(state: SignInState): SignInState {
  return {
    ...state,
    step: "identifier",
    error: null,
    passwordFallback: false,
    provider: null,
    challenge: null,
    // Only a code step leaves a live challenge behind. Coming back from the
    // password field retires nothing, because nothing was ever started.
    abandonChallengeId:
      state.step === "code" && state.challenge
        ? state.challenge.challenge_id
        : state.abandonChallengeId,
  };
}

export function failHandoff(state: SignInState, message: string): SignInState {
  return { ...state, step: "identifier", error: message, provider: null };
}

export type PasswordSignInResult =
  | { status: "OK" }
  | { status: "WRONG_CREDENTIALS_ERROR" }
  | { status: "SIGN_IN_NOT_ALLOWED"; reason: string }
  | { status: "FIELD_ERROR"; formFields: Array<{ id: string; error: string }> };

export type PasswordSignInOutcome =
  | { outcome: "authenticated" }
  | { outcome: "rejected"; message: string; fallback: boolean };

/**
 * Whether an account is passwordless, read from the refusal it produced.
 *
 * Prose-matching, and it should be unreachable: `/continue` routes a
 * passwordless address to a code and never offers it a password field. It is
 * still reachable two ways -- a browser Back into the password step, and login
 * methods changing between the two calls -- and being wrong here only costs an
 * offer of a fallback that is genuinely available, so matching loosely is the
 * safe direction.
 */
export function passwordFallbackFor(reason: string): boolean {
  return reason.toLowerCase().includes("email-code login");
}

export function signInErrorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "We couldn’t reach Lemma. Check your connection and try again.";
}

export async function runPasswordSignIn(
  signIn: () => Promise<PasswordSignInResult>,
): Promise<PasswordSignInOutcome> {
  let result: PasswordSignInResult;
  try {
    result = await signIn();
  } catch (cause) {
    // `runAuthRequest` has already turned a rate limit, an altcha failure and a
    // dead connection into readable prose on an `STGeneralError`. Reading the
    // message rather than a status is deliberate: the status never reaches here.
    return {
      outcome: "rejected",
      message: signInErrorMessage(cause),
      fallback: false,
    };
  }
  if (result.status === "OK") return { outcome: "authenticated" };
  if (result.status === "SIGN_IN_NOT_ALLOWED") {
    return {
      outcome: "rejected",
      message: result.reason,
      fallback: passwordFallbackFor(result.reason),
    };
  }
  if (result.status === "FIELD_ERROR") {
    return {
      outcome: "rejected",
      message: result.formFields[0]?.error || "Check your details and try again.",
      fallback: false,
    };
  }
  return {
    outcome: "rejected",
    message: "That password doesn’t match this email.",
    fallback: false,
  };
}

/**
 * Which of the two screens the prebuilt UI thinks it is on.
 *
 * Moved here out of `auth-portal.tsx` because it stopped being cosmetic. It
 * used to pick hero copy and hide a button; it now decides whether the whole
 * sign-in screen is ours, so the substring conventions it reverse-engineers
 * from SuperTokens deserve to be pinned by tests.
 */
export function resolveAuthMode(
  pathname: string,
  search: string,
  hash: string,
): AuthMode {
  const lowerPath = pathname.toLowerCase();
  if (lowerPath.includes("signup")) {
    return "signup";
  }
  if (lowerPath.includes("signin") || lowerPath.includes("login")) {
    return "signin";
  }

  const params = new URLSearchParams(search);
  const hashQueryIndex = hash.indexOf("?");
  const hashParams =
    hashQueryIndex >= 0
      ? new URLSearchParams(hash.slice(hashQueryIndex + 1))
      : new URLSearchParams();
  const lowerHash = hash.toLowerCase();
  const hashPath = lowerHash.split("?")[0];

  if (hashPath.includes("signup")) {
    return "signup";
  }
  if (hashPath.includes("signin") || hashPath.includes("login")) {
    return "signin";
  }

  const pageMarker = (
    params.get("page") || hashParams.get("page")
  )?.toLowerCase();

  if (pageMarker?.includes("up")) {
    return "signup";
  }
  if (pageMarker?.includes("in") || pageMarker?.includes("log")) {
    return "signin";
  }

  const modeMarker = (
    params.get("show") ||
    hashParams.get("show") ||
    params.get("mode") ||
    hashParams.get("mode") ||
    params.get("authMode") ||
    hashParams.get("authMode") ||
    params.get("auth") ||
    hashParams.get("auth")
  )?.toLowerCase();

  if (modeMarker?.includes("up")) {
    return "signup";
  }
  if (modeMarker?.includes("in")) {
    return "signin";
  }

  return "signin";
}

/** Hand the URL back to the prebuilt UI on the screen it owns. */
export function appendSignUpMarker(search: string): string {
  const params = new URLSearchParams(search);
  params.set("show", "signup");
  return `?${params.toString()}`;
}

/**
 * Whether this render belongs to the identifier-first screen.
 *
 * `isThirdPartyCallbackRoute` is not a nicety. `resolveAuthMode` answers
 * `"signin"` for `/callback/google` -- nothing in that path matches
 * `signup|signin|login`, so it falls through to the default -- and taking that
 * at face value would mount this screen over the OAuth callback, leaving the
 * authorization code unexchanged and Google sign-in silently broken. Keeping
 * those paths on the prebuilt handler also keeps `getRedirectionURL`'s SUCCESS
 * branch running for third-party, which is what carries the desktop and CLI
 * handoffs there.
 */
export function shouldRenderIdentifierFirst(input: {
  authMode: AuthMode;
  isThirdPartyCallbackRoute: boolean;
  routeIsHandledByPreBuiltUi: boolean;
}): boolean {
  return (
    input.authMode === "signin" &&
    !input.isThirdPartyCallbackRoute &&
    input.routeIsHandledByPreBuiltUi
  );
}
