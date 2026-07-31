import { useEffect, useMemo, type ReactNode } from "react";
import type { LemmaClient } from "../client.js";
import type { UserInfo } from "../auth.js";
import type { PodAccessStatus } from "./usePodAccess.js";
import { useAuth } from "./useAuth.js";
import { usePodAccess } from "./usePodAccess.js";
import {
  AppAccess,
  AppLoader,
  AppSignIn,
  type AppAppearance,
  type AppIdentity,
  type AppUserIdentity,
} from "./AppGate.js";

export interface AuthGuardLoadingContext {
  app: AppIdentity;
}

export interface AuthGuardUnauthenticatedContext {
  app: AppIdentity;
  signIn: () => void;
}

export interface AuthGuardAccessContext {
  app: AppIdentity;
  status: Exclude<PodAccessStatus, "idle" | "checking" | "member">;
  user: AppUserIdentity | null;
  isRequestingAccess: boolean;
  isCheckingAccess: boolean;
  error: Error | null;
  requestAccess: () => Promise<void>;
  refresh: () => Promise<PodAccessStatus>;
  switchAccount: () => Promise<void>;
}

type AuthGuardFallback<TContext> = ReactNode | ((context: TContext) => ReactNode);

export interface AuthGuardProps {
  client: LemmaClient;
  children: ReactNode;
  app?: AppIdentity;
  appName?: string;
  appDescription?: string;
  appIcon?: ReactNode;
  appearance?: AppAppearance;
  pendingRefreshIntervalMs?: number;
  loadingFallback?: AuthGuardFallback<AuthGuardLoadingContext>;
  unauthenticatedFallback?: AuthGuardFallback<AuthGuardUnauthenticatedContext>;
  accessRequestFallback?: AuthGuardFallback<AuthGuardAccessContext>;
}

function renderFallback<TContext>(
  fallback: AuthGuardFallback<TContext>,
  context: TContext,
): ReactNode {
  return typeof fallback === "function"
    ? (fallback as (value: TContext) => ReactNode)(context)
    : fallback;
}

function authUserIdentity(user: UserInfo | null): AppUserIdentity | null {
  if (!user) return null;
  return {
    email: user.email,
    name: typeof user.name === "string" ? user.name : null,
    first_name: typeof user.first_name === "string" ? user.first_name : null,
    last_name: typeof user.last_name === "string" ? user.last_name : null,
  };
}

export function AuthGuard({
  client,
  children,
  app: appOverride,
  appName,
  appDescription,
  appIcon,
  appearance,
  pendingRefreshIntervalMs = 8000,
  loadingFallback,
  unauthenticatedFallback,
  accessRequestFallback,
}: AuthGuardProps) {
  const { isLoading, isAuthenticated, user: authUser, redirectToAuth } = useAuth(client);
  const podAccess = usePodAccess({
    client,
    enabled: isAuthenticated && Boolean(client.podId),
  });

  const app = useMemo<AppIdentity>(() => {
    const runtime = client.app;
    return {
      name: appName ?? appOverride?.name ?? runtime?.name,
      description: appDescription ?? appOverride?.description ?? runtime?.description,
      icon: appIcon ?? appOverride?.icon,
      iconUrl: appOverride?.iconUrl ?? runtime?.iconUrl,
    };
  }, [appDescription, appIcon, appName, appOverride, client]);

  useEffect(() => {
    if (
      podAccess.status !== "pending"
      || pendingRefreshIntervalMs <= 0
      || typeof window === "undefined"
    ) {
      return;
    }
    const interval = window.setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        void podAccess.refresh();
      }
    }, pendingRefreshIntervalMs);
    return () => window.clearInterval(interval);
  }, [pendingRefreshIntervalMs, podAccess.refresh, podAccess.status]);

  const signIn = () => redirectToAuth();
  const switchAccount = async () => {
    await client.auth.redirectToFederatedLogout();
  };
  const requestAccess = async () => {
    try {
      await podAccess.requestAccess();
    } catch {
      return;
    }
  };

  const isPendingRefresh = podAccess.status === "checking"
    && podAccess.joinRequest?.status === "PENDING";
  const isCheckingAccess = isAuthenticated
    && Boolean(client.podId)
    && (podAccess.status === "idle" || podAccess.status === "checking")
    && !isPendingRefresh;

  if (isLoading || isCheckingAccess) {
    const context: AuthGuardLoadingContext = { app };
    return loadingFallback !== undefined
      ? <>{renderFallback(loadingFallback, context)}</>
      : <AppLoader app={app} appearance={appearance} />;
  }

  if (!isAuthenticated) {
    const context: AuthGuardUnauthenticatedContext = { app, signIn };
    return unauthenticatedFallback !== undefined
      ? <>{renderFallback(unauthenticatedFallback, context)}</>
      : <AppSignIn app={app} appearance={appearance} onSignIn={signIn} />;
  }

  if (!client.podId || podAccess.status === "member") {
    return <>{children}</>;
  }

  const accessStatus = podAccess.status === "pending" || isPendingRefresh
    ? "pending"
    : podAccess.status === "error"
      ? "error"
      : "missing";
  const user = podAccess.user ?? authUserIdentity(authUser);
  const context: AuthGuardAccessContext = {
    app,
    status: accessStatus,
    user,
    isRequestingAccess: podAccess.isRequestingAccess,
    isCheckingAccess: podAccess.isLoading,
    error: podAccess.error,
    requestAccess,
    refresh: podAccess.refresh,
    switchAccount,
  };

  if (accessRequestFallback !== undefined) {
    return <>{renderFallback(accessRequestFallback, context)}</>;
  }

  return (
    <AppAccess
      app={app}
      appearance={appearance}
      status={accessStatus}
      user={user}
      isSubmitting={podAccess.isRequestingAccess || podAccess.isLoading}
      error={podAccess.error}
      onRequestAccess={requestAccess}
      onRefresh={() => void podAccess.refresh()}
      onSwitchAccount={switchAccount}
    />
  );
}
