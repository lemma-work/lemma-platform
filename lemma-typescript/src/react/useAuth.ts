import { useState, useEffect } from "react"; // peer dependency
import type { LemmaClient } from "../client.js";
import type { AuthState, BuildAuthUrlOptions } from "../auth.js";
import { reconnectDelay } from "../reachability.js";

type RedirectToAuthOptions = Omit<BuildAuthUrlOptions, "redirectUri"> & { redirectUri?: string };

export interface UseAuthResult {
  status: AuthState["status"];
  user: AuthState["user"];
  isLoading: boolean;
  isAuthenticated: boolean;
  /** The API did not answer; the hook is retrying on its own. */
  isUnreachable: boolean;
  redirectToAuth: (options?: RedirectToAuthOptions) => void;
}

/**
 * React hook for subscribing to Lemma auth state.
 *
 * Usage:
 *   const { isAuthenticated, isLoading, redirectToAuth } = useAuth(client);
 */
export function useAuth(client: LemmaClient): UseAuthResult {
  const [state, setState] = useState<AuthState>(client.auth.getState());

  useEffect(() => {
    // Subscribe to future state changes
    const unsubscribe = client.auth.subscribe((next) => setState(next));

    // If still in loading state, trigger the auth check
    if (state.status === "loading") {
      client.auth.checkAuth().catch(() => {
        // checkAuth already handles errors internally
      });
    }

    return unsubscribe;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  // Unreachable is not signed out: wait for the API to answer again, then ask
  // about the session. The liveness probe gates the retry so an outage does
  // not spend the refresh breaker's budget and trip it into a sign-out.
  const unreachable = state.status === "unreachable";
  useEffect(() => {
    if (!unreachable) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = (count: number) => {
      timer = setTimeout(async () => {
        if (cancelled) return;
        if (await client.auth.isReachable()) {
          if (!cancelled) void client.auth.checkAuth().catch(() => undefined);
          return;
        }
        if (!cancelled) attempt(count + 1);
      }, reconnectDelay(count));
    };
    attempt(0);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [client, unreachable]);

  return {
    status: state.status,
    user: state.user,
    isLoading: state.status === "loading",
    isAuthenticated: state.status === "authenticated",
    isUnreachable: unreachable,
    redirectToAuth: (options?: RedirectToAuthOptions) => client.auth.redirectToAuth(options),
  };
}
