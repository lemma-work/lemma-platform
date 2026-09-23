"use client";

import Session from "supertokens-auth-react/recipe/session";

import { forgetBrowserSession } from "@/lib/auth/browser-session";

// The pure half lives in `lib/auth/browser-session` so that `logoutToHome`,
// which every route with a sign-out reaches, does not pull this module — and
// the SuperTokens React SDK it imports — into its chunk. Re-exported here so
// the auth portal has one place to import from.
export {
  clearFrontendSessionState,
  cookieDomainsFor,
  forgetBrowserSession,
  isUnrepairableSessionFailure,
  type SessionStateStore,
} from "@/lib/auth/browser-session";

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
