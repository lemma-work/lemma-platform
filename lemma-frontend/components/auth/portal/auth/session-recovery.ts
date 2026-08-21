"use client";

import Session from "supertokens-auth-react/recipe/session";

/**
 * Where the SuperTokens frontend SDK keeps the state that makes
 * `doesSessionExist()` answer true.
 *
 * The tokens themselves are HttpOnly and cannot be touched from here — but they
 * are not what the app reads. `sFrontToken` is, and while it is present the app
 * renders a signed-in screen no matter what the API answers. Clearing these is
 * what lets a session the server will not accept stop being presented as one.
 */
const FRONTEND_SESSION_KEYS = [
  "sFrontToken",
  "sAntiCsrf",
  "sIRTFrontend",
  "st-access-token",
  "st-refresh-token",
  "st-last-access-token-update",
] as const;

/**
 * Somewhere the SDK's session state lives, with the DOM taken out so the
 * clearing loop can be asserted on.
 */
export type SessionStateStore = {
  /** Every host the current page could hold a cookie on, widest last. */
  cookieDomains: readonly string[];
  expireCookie(name: string, domain: string | null): void;
  removeStored(name: string): void;
};

/**
 * Forget the session this browser thinks it has.
 *
 * Returns the keys it acted on, which is the whole of what a test can observe:
 * the browser decides whether a cookie actually goes, and it will not report
 * back.
 */
export function clearFrontendSessionState(
  store: SessionStateStore,
): readonly string[] {
  for (const key of FRONTEND_SESSION_KEYS) {
    store.removeStored(key);
    // Host-only first, then each parent the cookie could have been widened to.
    // A cookie set on `.example.com` is invisible to a delete aimed at
    // `app.example.com`, and the SDK's own domain setting is not readable here.
    store.expireCookie(key, null);
    for (const domain of store.cookieDomains) {
      store.expireCookie(key, domain);
    }
  }
  return FRONTEND_SESSION_KEYS;
}

/**
 * Did this failure come from a session that refreshing cannot repair?
 *
 * The SDK refreshes and retries a 401 up to `maxRetryAttemptsForSessionRefresh`
 * times and then throws this. Reaching it means the refresh kept succeeding —
 * so the session is real — while the access token it produced authorized
 * nothing. No amount of further refreshing changes that, and treating it as a
 * transient error is what leaves the app signed in and unable to do anything.
 */
export function isUnrepairableSessionFailure(error: unknown): boolean {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "";
  return message.includes("The maximum session refresh limit has been reached");
}

/** Every domain a cookie on this host could have been scoped to. */
export function cookieDomainsFor(hostname: string): readonly string[] {
  const labels = hostname.split(".");
  const domains: string[] = [];
  // Stop before the last label: a cookie on a bare public suffix is not one the
  // browser would have accepted, so asking it to delete one is noise.
  for (let index = 0; index < labels.length - 1; index += 1) {
    domains.push(`.${labels.slice(index).join(".")}`);
  }
  return domains;
}

function browserSessionStore(): SessionStateStore {
  return {
    cookieDomains: cookieDomainsFor(window.location.hostname),
    expireCookie(name, domain) {
      const scope = domain ? `; domain=${domain}` : "";
      document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax${scope}`;
    },
    removeStored(name) {
      try {
        window.localStorage.removeItem(name);
        window.sessionStorage.removeItem(name);
      } catch {
        // Storage can be denied outright (private mode, a locked-down
        // webview). The cookie half is the one that matters; losing this one
        // must not stop it.
      }
    },
  };
}

/**
 * Sign out even when the API will not let us.
 *
 * `Session.signOut()` is itself an authorized call, so the one state a user
 * most needs to escape — a session every authorized route rejects — is exactly
 * the state in which it throws. Every caller that only awaited it left the
 * user on a screen whose sign-out button did nothing.
 */
export async function abandonSession(): Promise<void> {
  try {
    await Session.signOut();
  } catch {
    // Expected when the session is the thing that is broken. The local clear
    // below is what actually gets the user back to a sign-in screen.
  }
  forgetBrowserSession();
}

/**
 * The local half on its own, for callers that already made their own sign-out
 * call and only need this browser to stop presenting the session afterwards.
 */
export function forgetBrowserSession(): void {
  if (typeof window === "undefined") return;
  clearFrontendSessionState(browserSessionStore());
}
