"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { SuperTokensWrapper } from "supertokens-auth-react";
import { canHandleRoute, getRoutingComponent } from "supertokens-auth-react/ui";
import Session, {
  useSessionContext,
} from "supertokens-auth-react/recipe/session";
import { EmailPasswordPreBuiltUI } from "supertokens-auth-react/recipe/emailpassword/prebuiltui";
import { ThirdPartyPreBuiltUI } from "supertokens-auth-react/recipe/thirdparty/prebuiltui";
import { EmailVerificationPreBuiltUI } from "supertokens-auth-react/recipe/emailverification/prebuiltui";
import EmailVerification from "supertokens-auth-react/recipe/emailverification";

import { authConfig, buildApiUrl, refreshSessionPath } from "@/components/auth/portal/auth/config";
import {
  clearStoredRedirectUri,
  consumeStoredRedirectUri,
  getDefaultPostAuthRedirect,
  getStoredRedirectUri,
  hasRedirectUriInSearch,
  normaliseLoopbackRedirectUri,
  readRawRedirectUriFromSearch,
  readRedirectUriFromSearch,
} from "@/components/auth/portal/auth/redirects";
import {
  challengeForDesktopVerifier,
  clearPendingDesktopAuth,
  clearStoredDesktopRequestId,
  createDesktopVerifier,
  getPendingDesktopAuth,
  getStoredDesktopRequestId,
  isLemmaDesktop,
  readDesktopRequestIdFromSearch,
  storeDesktopRequestId,
  storePendingDesktopAuth,
  type PendingDesktopAuth,
} from "@/components/auth/portal/auth/desktop";
import {
  ensureSuperTokensInit,
  isTelegramMiniApp,
} from "@/components/auth/portal/auth/supertokens";
import { VerificationScreen } from "@/components/auth/portal/auth/verification-screen";
import {
  getDestinationLabel,
  getRedirectDestinationFallback,
  getRejectedRedirectDestination,
  readWindowUrlSnapshot,
  useRedirectDestinationMetadata,
  type UrlSnapshot,
} from "@/components/auth/portal/auth-portal-metadata";
import {
  AuthScreenLayout,
  DestinationPanel,
  LoadingState,
  PendingPanel,
  RedirectStateSync,
  StatusPanel,
  defaultHeroCopy,
  type HeroCopy,
} from "@/components/auth/portal/auth-portal-chrome";

export {
  defaultHeroCopy,
  destinationHeroCopy,
} from "@/components/auth/portal/auth-portal-chrome";
export type { HeroCopy } from "@/components/auth/portal/auth-portal-chrome";

type CurrentUser = {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  is_verified: boolean;
};

type CliSessionResponse = {
  access_token: string;
  refresh_token: string;
  access_token_expires_at: number;
  session_handle: string;
  user_id: string;
  email: string;
  token_type: string;
};

type AuthMode = "signin" | "signup";

const preBuiltUiList = [
  EmailPasswordPreBuiltUI,
  EmailVerificationPreBuiltUI,
  ThirdPartyPreBuiltUI,
] as const;

function TelegramLoginButton({ visible }: { visible: boolean }) {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    void fetch(buildApiUrl("/auth/telegram/config"))
      .then((response) => (response.ok ? response.json() : { enabled: false }))
      .then((payload: { enabled?: boolean }) => {
        if (active) setEnabled(payload.enabled === true);
      });
    return () => {
      active = false;
    };
  }, [visible]);

  if (!visible || !enabled) return null;
  return (
    <button
      type="button"
      className="secondary-button auth-portal-session-button"
      onClick={() => {
        const start = new URL(buildApiUrl("/auth/telegram/start"));
        start.searchParams.set("purpose", "signin");
        start.searchParams.set("return_to", window.location.href);
        window.location.assign(start.toString());
      }}
    >
      Sign in with Telegram
    </button>
  );
}

function resolveAuthMode(
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

function AuthLanding() {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useSessionContext();
  const doesSessionExist = session.loading ? false : session.doesSessionExist;
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [isFetchingUser, setIsFetchingUser] = useState(false);
  const [desktopCompletion, setDesktopCompletion] = useState<
    "idle" | "completing" | "complete" | "error"
  >("idle");
  const desktopCompletionPromiseRef = useRef<Promise<void> | null>(null);
  const [urlSnapshot, setUrlSnapshot] = useState<UrlSnapshot>(() =>
    readWindowUrlSnapshot(),
  );

  useEffect(() => {
    const syncUrlSnapshot = () => {
      const next = readWindowUrlSnapshot();
      setUrlSnapshot((current) => {
        if (
          current.pathname === next.pathname &&
          current.search === next.search &&
          current.hash === next.hash
        ) {
          return current;
        }

        return next;
      });
    };

    syncUrlSnapshot();

    window.addEventListener("popstate", syncUrlSnapshot);
    window.addEventListener("hashchange", syncUrlSnapshot);
    const pollId = window.setInterval(syncUrlSnapshot, 220);

    return () => {
      window.removeEventListener("popstate", syncUrlSnapshot);
      window.removeEventListener("hashchange", syncUrlSnapshot);
      window.clearInterval(pollId);
    };
  }, []);

  const liveUrlSnapshot = readWindowUrlSnapshot();
  const effectiveSearch =
    location.search || urlSnapshot.search || liveUrlSnapshot.search;
  const effectivePathname =
    location.pathname || urlSnapshot.pathname || liveUrlSnapshot.pathname;
  const effectiveHash =
    location.hash || urlSnapshot.hash || liveUrlSnapshot.hash;
  const rawRedirectUri = readRawRedirectUriFromSearch(effectiveSearch);
  const queryDesktopRequestId = readDesktopRequestIdFromSearch(effectiveSearch);
  const desktopRequestId =
    queryDesktopRequestId || getStoredDesktopRequestId();
  const queryRedirectUri = readRedirectUriFromSearch(effectiveSearch);
  const hasExplicitRedirectUri = hasRedirectUriInSearch(effectiveSearch);
  const redirectUri =
    queryRedirectUri ||
    (hasExplicitRedirectUri ? null : getStoredRedirectUri());
  const acceptedDestination = useRedirectDestinationMetadata(redirectUri);
  const rejectedDestination = queryRedirectUri
    ? null
    : getRejectedRedirectDestination(rawRedirectUri);
  const destination = acceptedDestination || rejectedDestination;
  const destinationLabel =
    destination?.name || getDestinationLabel(redirectUri);
  const authMode = resolveAuthMode(
    effectivePathname,
    effectiveSearch,
    effectiveHash,
  );
  const isEmailVerificationRoute = effectivePathname
    .toLowerCase()
    .endsWith("/verify-email");
  const authHeroCopy: HeroCopy =
    isEmailVerificationRoute
      ? {
          eyebrow: "Account security",
          title: "One quick verification.",
          description:
            "Confirm your email once, then continue securely with your Lemma account.",
        }
      : authMode === "signup"
      ? {
          eyebrow: "Create account for",
          title: destinationLabel || "Create your Lemma identity",
          description: destinationLabel
            ? `Create your Lemma account, then head back to ${destinationLabel}.`
            : "Set up your Lemma account once, then use it wherever you work.",
        }
      : {
          eyebrow: "Continue to",
          title: destinationLabel || "Sign in to Lemma",
          description: destinationLabel
            ? `Sign in with Lemma, then head back to ${destinationLabel}.`
            : "Sign in once and continue with your Lemma account.",
        };

  useEffect(() => {
    if (queryDesktopRequestId) {
      storeDesktopRequestId(queryDesktopRequestId);
    }
  }, [queryDesktopRequestId]);

  useEffect(() => {
    if (session.loading) {
      return;
    }

    if (
      !doesSessionExist &&
      location.pathname !== "/" &&
      !canHandleRoute([...preBuiltUiList])
    ) {
      navigate({ pathname: "/", search: location.search }, { replace: true });
    }
  }, [
    doesSessionExist,
    location.pathname,
    location.search,
    navigate,
    session.loading,
  ]);

  useEffect(() => {
    if (session.loading || !doesSessionExist || desktopRequestId) {
      return;
    }

    let isActive = true;
    setIsFetchingUser(true);

    fetch(buildApiUrl("/users/me"), {
      credentials: "include",
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Unable to load user: ${response.status}`);
        }
        return (await response.json()) as CurrentUser;
      })
      .then((user) => {
        if (isActive) {
          setCurrentUser(user);
        }
      })
      .catch(() => {
        if (isActive) {
          setCurrentUser(null);
        }
      })
      .finally(() => {
        if (isActive) {
          setIsFetchingUser(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, [desktopRequestId, doesSessionExist, session.loading]);

  useEffect(() => {
    if (session.loading || !doesSessionExist || !isTelegramMiniApp()) {
      return;
    }

    let cancelled = false;
    let observedUnverified = false;
    const checkVerification = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const result = await EmailVerification.isEmailVerified();
        if (!result.isVerified) {
          observedUnverified = true;
          return;
        }
        if (observedUnverified && !cancelled) {
          await Session.attemptRefreshingSession();
          if (!cancelled) window.location.reload();
        }
      } catch {
        // A transient network failure is retried on the next poll/focus event.
      }
    };

    const handleReturn = () => void checkVerification();
    void checkVerification();
    const pollId = window.setInterval(handleReturn, 3000);
    window.addEventListener("focus", handleReturn);
    document.addEventListener("visibilitychange", handleReturn);
    return () => {
      cancelled = true;
      window.clearInterval(pollId);
      window.removeEventListener("focus", handleReturn);
      document.removeEventListener("visibilitychange", handleReturn);
    };
  }, [doesSessionExist, session.loading]);

  useEffect(() => {
    if (session.loading || !doesSessionExist || desktopRequestId) {
      return;
    }

    const finalRedirectUri = queryRedirectUri || consumeStoredRedirectUri();

    if (!finalRedirectUri) {
      return;
    }

    window.location.replace(finalRedirectUri);
  }, [
    desktopRequestId,
    doesSessionExist,
    queryRedirectUri,
    redirectUri,
    session.loading,
  ]);

  useEffect(() => {
    if (session.loading || !doesSessionExist || !desktopRequestId) {
      return;
    }

    let cancelled = false;
    if (!desktopCompletionPromiseRef.current) {
      setDesktopCompletion("completing");
      desktopCompletionPromiseRef.current = fetch(
        buildApiUrl(
          `/auth/desktop/requests/${encodeURIComponent(desktopRequestId)}/complete`,
        ),
        { method: "POST", credentials: "include" },
      ).then((response) => {
        if (!response.ok) {
          throw new Error(`Unable to complete desktop login: ${response.status}`);
        }
      });
    }

    void desktopCompletionPromiseRef.current
      .then(() => {
        if (cancelled) return;
        setDesktopCompletion("complete");
        clearStoredDesktopRequestId();
        window.setTimeout(() => {
          window.location.assign(
            `lemma://auth/complete?request_id=${encodeURIComponent(desktopRequestId)}`,
          );
        }, 350);
      })
      .catch(() => {
        if (!cancelled) setDesktopCompletion("error");
      });

    return () => {
      cancelled = true;
    };
  }, [desktopRequestId, doesSessionExist, session.loading]);

  if (session.loading) {
    return (
      <LoadingState
        message="Checking your session…"
        destination={destination}
      />
    );
  }

  if (isEmailVerificationRoute) {
    return (
      <AuthScreenLayout destination={destination} heroCopy={authHeroCopy}>
        <div className="auth-form-stack">
          <VerificationScreen doesSessionExist={doesSessionExist} />
        </div>
      </AuthScreenLayout>
    );
  }

  if (doesSessionExist && desktopRequestId) {
    const failed = desktopCompletion === "error";
    return (
      <AuthScreenLayout
        heroCopy={{
          eyebrow: "Lemma Desktop",
          title: "Return to the app.",
          description: "Your browser session is being handed back securely.",
        }}
      >
        <StatusPanel
          eyebrow={failed ? "Handoff failed" : "Signed in"}
          title={failed ? "We couldn't reach Lemma Desktop." : "You're signed in."}
          description={
            failed
              ? "Return to Lemma Desktop and start the sign-in again."
              : "Lemma Desktop will come back to the foreground and finish opening your workspace."
          }
          tone={failed ? "danger" : "neutral"}
        >
          {!failed ? <div className="spinner" aria-hidden="true" /> : null}
        </StatusPanel>
      </AuthScreenLayout>
    );
  }

  if (doesSessionExist && redirectUri) {
    return (
      <AuthScreenLayout destination={destination}>
        <PendingPanel
          message={
            destinationLabel
              ? `Taking you back to ${destinationLabel}…`
              : "Taking you back…"
          }
        />
      </AuthScreenLayout>
    );
  }

  if (doesSessionExist) {
    return (
      <AuthScreenLayout destination={destination}>
        <section className="session-state">
          <div className="session-panel">
            <span className="panel-label">Signed in as</span>
            <strong>
              {currentUser?.email ||
                (isFetchingUser ? "Loading your profile…" : "Your account")}
            </strong>
            <p>
              {currentUser?.first_name
                ? `Welcome back, ${currentUser.first_name}.`
                : "You're signed in to Lemma."}
            </p>
          </div>

          <div className="button-row">
            <button
              type="button"
              className="primary-button auth-portal-session-button"
              onClick={() => {
                window.location.replace(
                  consumeStoredRedirectUri() || getDefaultPostAuthRedirect(),
                );
              }}
            >
              Continue
            </button>

            <button
              type="button"
              className="secondary-button auth-portal-session-button"
              onClick={() => {
                void Session.signOut().then(() => {
                  clearStoredRedirectUri();
                  setCurrentUser(null);
                  window.location.replace(getDefaultPostAuthRedirect());
                });
              }}
            >
              Sign out
            </button>
          </div>

          {isFetchingUser ? (
            <p className="helper-copy">Refreshing your profile snapshot...</p>
          ) : null}
        </section>
      </AuthScreenLayout>
    );
  }

  if (!canHandleRoute([...preBuiltUiList])) {
    return <LoadingState message="Preparing sign-in…" />;
  }

  return (
    <AuthScreenLayout destination={destination} heroCopy={authHeroCopy}>
      <div className="auth-form-stack">
        <TelegramLoginButton visible={authMode === "signin"} />
        {getRoutingComponent([...preBuiltUiList])}
      </div>
    </AuthScreenLayout>
  );
}

function DesktopSignInPage() {
  const session = useSessionContext();
  const doesSessionExist = session.loading ? false : session.doesSessionExist;
  const [pending, setPending] = useState<PendingDesktopAuth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startPromiseRef = useRef<Promise<PendingDesktopAuth> | null>(null);
  const exchangePromiseRef = useRef<Promise<void> | null>(null);
  const automaticallyOpenedRequestRef = useRef<string | null>(null);

  const openBrowser = (browserUrl: string, force = false) => {
    // The desktop shell intercepts this marked first-party navigation, opens it
    // in the system browser, and cancels navigation in the webview.
    if (!force && automaticallyOpenedRequestRef.current === browserUrl) return;
    automaticallyOpenedRequestRef.current = browserUrl;
    window.location.assign(browserUrl);
  };

  useEffect(() => {
    if (session.loading) return;
    if (doesSessionExist) {
      window.location.replace(
        consumeStoredRedirectUri() || getDefaultPostAuthRedirect(),
      );
      return;
    }

    let cancelled = false;
    const existing = getPendingDesktopAuth();
    if (existing) {
      setPending(existing);
      openBrowser(existing.browserUrl);
      return;
    }

    if (!startPromiseRef.current) {
      startPromiseRef.current = (async () => {
        const verifier = createDesktopVerifier();
        const codeChallenge = await challengeForDesktopVerifier(verifier);
        const response = await fetch(buildApiUrl("/auth/desktop/requests"), {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code_challenge: codeChallenge }),
        });
        if (!response.ok) {
          throw new Error(`Unable to start desktop login: ${response.status}`);
        }
        const created = (await response.json()) as {
          request_id: string;
          expires_in_seconds: number;
        };
        const browserUrl = new URL(authConfig.websiteBasePath, window.location.origin);
        browserUrl.searchParams.set("desktop_browser", "1");
        browserUrl.searchParams.set("desktop_request", created.request_id);
        const mode = new URLSearchParams(window.location.search).get("mode");
        if (mode === "signup") browserUrl.searchParams.set("show", "signup");
        const next: PendingDesktopAuth = {
          requestId: created.request_id,
          verifier,
          browserUrl: browserUrl.toString(),
          expiresAt: Date.now() + created.expires_in_seconds * 1000,
        };
        storePendingDesktopAuth(next);
        return next;
      })();
    }

    void startPromiseRef.current
      .then((next) => {
        if (cancelled) return;
        setPending(next);
        openBrowser(next.browserUrl);
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Unable to start sign-in");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [doesSessionExist, session.loading]);

  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    if (!exchangePromiseRef.current) {
      exchangePromiseRef.current = (async () => {
        while (Date.now() < pending.expiresAt) {
          const response = await fetch(buildApiUrl("/auth/desktop/session"), {
            method: "POST",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
              "st-auth-mode": "cookie",
            },
            body: JSON.stringify({
              request_id: pending.requestId,
              code_verifier: pending.verifier,
            }),
          });
          if (response.status === 409) {
            await new Promise((resolve) => window.setTimeout(resolve, 1200));
            continue;
          }
          if (!response.ok) {
            throw new Error(
              response.status === 404
                ? "This sign-in request expired. Start again."
                : `Unable to finish desktop login: ${response.status}`,
            );
          }
          return;
        }
        throw new Error("This sign-in request expired. Start again.");
      })();
    }

    void exchangePromiseRef.current
      .then(() => {
        clearPendingDesktopAuth();
        if (!cancelled) {
          window.location.replace(
            consumeStoredRedirectUri() || getDefaultPostAuthRedirect(),
          );
        }
      })
      .catch((cause) => {
        clearPendingDesktopAuth();
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Unable to finish sign-in");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pending]);

  if (session.loading) {
    return <LoadingState message="Checking your desktop session…" />;
  }

  return (
    <AuthScreenLayout
      heroCopy={{
        eyebrow: "Lemma Desktop",
        title: "Sign in with your browser.",
        description:
          "Your browser handles account security; Lemma Desktop opens automatically when you're done.",
      }}
    >
      {error ? (
        <StatusPanel
          eyebrow="Sign-in stopped"
          title="Let's try that again."
          description={error}
          tone="danger"
        >
          <button
            type="button"
            className="primary-button auth-portal-session-button"
            onClick={() => {
              clearPendingDesktopAuth();
              window.location.reload();
            }}
          >
            Start again
          </button>
        </StatusPanel>
      ) : (
        <StatusPanel
          eyebrow="Browser sign-in"
          title="Finish signing in in your browser."
          description="This window is waiting securely and will update as soon as authentication completes."
        >
          <div className="button-row">
            <button
              type="button"
              className="secondary-button auth-portal-session-button"
              disabled={!pending}
              onClick={() => pending && openBrowser(pending.browserUrl, true)}
            >
              Open browser again
            </button>
          </div>
        </StatusPanel>
      )}
    </AuthScreenLayout>
  );
}

function DesktopAwareAuthLanding() {
  return isLemmaDesktop() ? <DesktopSignInPage /> : <AuthLanding />;
}

function RefreshSessionPage() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    let cancelled = false;

    void Session.attemptRefreshingSession().then((success) => {
      if (cancelled) {
        return;
      }

      if (success) {
        const redirectUri = readRedirectUriFromSearch(location.search);
        const fallback = getDefaultPostAuthRedirect();
        window.location.replace(redirectUri || fallback);
        return;
      }

      navigate({ pathname: "/", search: location.search }, { replace: true });
    });

    return () => {
      cancelled = true;
    };
  }, [location.search, navigate]);

  return <LoadingState message="Refreshing your session…" />;
}

function CliLoginPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const session = useSessionContext();
  const hasSubmittedRef = useRef(false);
  const hasRedirectedToSignInRef = useRef(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState(
    "Preparing CLI sign-in...",
  );

  const params = new URLSearchParams(location.search);
  const callbackUrl = normaliseLoopbackRedirectUri(params.get("callback"));
  const state = params.get("state");
  const doesSessionExist = session.loading ? false : session.doesSessionExist;
  const callbackDestination = getRedirectDestinationFallback(callbackUrl);

  useEffect(() => {
    if (session.loading) {
      return;
    }

    if (!callbackUrl || !state) {
      setErrorMessage(
        "This CLI login link is invalid. Start the flow again from `lemma auth login`.",
      );
      return;
    }

    if (!doesSessionExist || hasSubmittedRef.current) {
      return;
    }

    hasSubmittedRef.current = true;
    setStatusMessage("Creating a CLI session...");

    void fetch(
      buildApiUrl("/auth/cli/session-tokens"),
      {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
        },
      },
    )
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Unable to create CLI session: ${response.status}`);
        }

        const sessionPayload = (await response.json()) as CliSessionResponse;
        setStatusMessage(
          "Sending your CLI session back to the local callback...",
        );

        const callbackResponse = await fetch(callbackUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            state,
            session: {
              ...sessionPayload,
              base_url: authConfig.apiRequestBaseUrl,
            },
          }),
        });

        if (!callbackResponse.ok) {
          throw new Error(
            `Local callback rejected the session: ${callbackResponse.status}`,
          );
        }

        setStatusMessage("CLI login complete. You can close this window.");
      })
      .catch((error) => {
        setErrorMessage(
          error instanceof Error ? error.message : "CLI login failed.",
        );
      });
  }, [callbackUrl, doesSessionExist, session.loading, state]);

  useEffect(() => {
    if (
      session.loading ||
      doesSessionExist ||
      hasRedirectedToSignInRef.current
    ) {
      return;
    }

    const signInRedirect = new URL(window.location.origin);
    signInRedirect.searchParams.set("redirect_uri", window.location.href);
    hasRedirectedToSignInRef.current = true;
    navigate(
      {
        pathname: "/",
        search: signInRedirect.search,
      },
      { replace: true },
    );
  }, [doesSessionExist, navigate, session.loading]);

  if (session.loading) {
    return (
      <AuthScreenLayout destination={callbackDestination}>
        <PendingPanel
          message="Checking your session…"
          detail="Preparing the secure local handoff."
        />
      </AuthScreenLayout>
    );
  }

  if (errorMessage) {
    return (
      <AuthScreenLayout destination={callbackDestination}>
        <StatusPanel
          eyebrow="Unable to continue"
          title="CLI login couldn't complete."
          description={errorMessage}
          tone="danger"
        >
          <div className="status-inline status-inline-danger">
            <p className="helper-copy">
              Start the flow again from <code>lemma auth login</code> after
              correcting the link or callback.
            </p>
          </div>
        </StatusPanel>
      </AuthScreenLayout>
    );
  }

  if (doesSessionExist) {
    return (
      <AuthScreenLayout destination={callbackDestination}>
        <PendingPanel
          message={statusMessage}
          detail="You can close this window once the CLI confirms completion."
        />
      </AuthScreenLayout>
    );
  }

  return (
    <AuthScreenLayout destination={callbackDestination}>
      <PendingPanel
        message="Redirecting to sign-in…"
        detail="After sign-in, we'll resume the CLI handoff automatically."
      />
    </AuthScreenLayout>
  );
}

function AppShell() {
  return (
    <div className="auth-portal-root page-shell">
      <main className="simple-auth-shell">
        <Routes>
          <Route path="/cli/login" element={<CliLoginPage />} />
          <Route path="/desktop" element={<DesktopSignInPage />} />
          <Route path={refreshSessionPath} element={<RefreshSessionPage />} />
          <Route path="*" element={<DesktopAwareAuthLanding />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    ensureSuperTokensInit();
    setIsMounted(true);
  }, []);

  if (!isMounted) {
    return (
      <div className="auth-portal-root page-shell">
        <main className="simple-auth-shell">
          <section className="auth-screen">
            <section className="visual-panel">
              <DestinationPanel heroCopy={defaultHeroCopy} />
            </section>
            <aside className="auth-panel-shell">
              <PendingPanel message="Preparing sign-in…" />
            </aside>
          </section>
        </main>
      </div>
    );
  }

  return (
    <SuperTokensWrapper>
      <BrowserRouter
        basename={
          authConfig.websiteBasePath === "/" ? undefined : authConfig.websiteBasePath
        }
      >
        <RedirectStateSync />
        <AppShell />
      </BrowserRouter>
    </SuperTokensWrapper>
  );
}
