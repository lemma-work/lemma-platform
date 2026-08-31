"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { buildApiUrl } from "@/components/auth/portal/auth/config";
import { isLocalDeployment } from "@/lib/config";

/**
 * Whether this installation has a working AI provider.
 *
 * `needs_setup` is the backend's own answer, derived from `LEMMA_LOCAL_AI_READY`
 * — which locald sets from the operator config and nothing else. An
 * organization-scoped provider profile created through the hosted flow does not
 * move it, which is exactly why onboarding has to gate on this rather than on
 * its own idea of whether the user connected something.
 */
export type LocalAiStatus = "unknown" | "ready" | "needs_setup";

type CapabilityHealth = {
  capabilities?: {
    ai_profile?: {
      status?: string;
    };
  };
};

export async function readLocalAiStatus(): Promise<LocalAiStatus> {
  try {
    const response = await fetch(buildApiUrl("/health/capabilities"), {
      credentials: "include",
      cache: "no-store",
    });
    if (!response.ok) return "unknown";
    const health = (await response.json()) as CapabilityHealth;
    const status = health.capabilities?.ai_profile?.status;
    if (status === "ready") return "ready";
    if (status === "needs_setup") return "needs_setup";
    return "unknown";
  } catch {
    // The backend restarts when a provider is applied, so a failed probe is
    // the expected state for a few seconds mid-setup, not an error worth
    // showing. Callers keep polling.
    return "unknown";
  }
}

/**
 * Poll the AI capability while a setup step is waiting on it.
 *
 * Local settings is a separate webview, so the workspace gets no callback when
 * the user finishes configuring a provider in it — and applying one restarts
 * the backend, so the answer is briefly unreachable either way. Polling is what
 * turns "they went off and did it" into something the step can act on.
 */
export function useLocalAiStatus(active: boolean, intervalMs = 2000) {
  const [status, setStatus] = useState<LocalAiStatus>("unknown");

  const refresh = useCallback(async () => {
    setStatus(await readLocalAiStatus());
  }, []);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const tick = async () => {
      const next = await readLocalAiStatus();
      if (!cancelled) setStatus(next);
    };
    void tick();
    const timer = window.setInterval(() => void tick(), intervalMs);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active, intervalMs]);

  return { status, refresh };
}

export type AiProfileDraft = {
  protocol: "openai_compat" | "anthropic_compat";
  base_url: string;
  default_model: string;
  models: string[];
  vision_models: string[];
  allow_private_network: boolean;
};

function bridge() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (typeof invoke !== "function") {
    throw new Error("This can only be done in the Lemma desktop app.");
  }
  return invoke;
}

/** Ask a provider what it can run. Writes nothing. */
export async function discoverProviderModels(
  ai: AiProfileDraft,
  apiKey?: string,
): Promise<string[]> {
  const models = await bridge()("discover_provider_models", {
    payload: { ai, ...(apiKey === undefined ? {} : { api_key: apiKey }) },
  });
  return Array.isArray(models) ? (models as string[]) : [];
}

/**
 * Point this installation at a provider.
 *
 * Resolves only once the provider validated and the backend came back up, so
 * the caller can show one honest spinner rather than polling and guessing.
 */
export async function configureAiProvider(
  ai: AiProfileDraft,
  apiKey?: string,
): Promise<void> {
  await bridge()("configure_ai_provider", {
    payload: { ai, ...(apiKey === undefined ? {} : { api_key: apiKey }) },
  });
}

/** Open Local settings at a page. Returns false when the bridge is unavailable. */
export async function openLocalSettings(page: string): Promise<boolean> {
  const invoke = window.__TAURI__?.core?.invoke;
  if (typeof invoke !== "function") return false;
  try {
    await invoke("open_control_center", { page });
    return true;
  } catch {
    return false;
  }
}

/**
 * Is the privileged desktop bridge reachable from this page?
 *
 * False in a LAN or public-link browser: those origins are deliberately absent
 * from the desktop capability, so the steps that hand off to Local settings
 * have to degrade to an instruction rather than a dead button.
 *
 * Read through `useSyncExternalStore` rather than called during render. The
 * plain call was wrong in the one place it mattered most: on the server there
 * is no `window`, so it answered "no bridge" and rendered "this has to be done
 * on the computer running Lemma" into the HTML — which the user then read
 * while sitting at that computer, in the desktop app.
 *
 * The shell injects its globals in an initialization script that runs before
 * any page script and never removes them, so there is genuinely nothing to
 * subscribe to; what this buys is a correct client snapshot and a server
 * snapshot React will reconcile rather than keep.
 */
function subscribeDesktopBridge() {
  return () => {};
}

function readDesktopBridge(): boolean {
  // Local-ness comes from the page, not from the shell's `mode`.
  //
  // `window.__LEMMA_DESKTOP__.mode` is baked into the main window's
  // initialization script when the window is *built* — before the user has
  // chosen anything. Choosing local writes the config and starts the stack but
  // never recreates the webview, so the script keeps replaying the launch-time
  // mode for the rest of the session. On a first run the workspace therefore
  // loaded with `mode` still unset, this returned false, and the setup steps
  // told someone sitting in the desktop app that they had to do it in the
  // desktop app — with Continue disabled. A restart fixed it, which is exactly
  // why it survived.
  //
  // `DEPLOYMENT` is rendered by the frontend the local stack itself serves, so
  // it cannot be older than the decision that started that stack. The `__TAURI__`
  // check still answers the other half: whether this page can reach the shell at
  // all, which is false in a LAN or public-link browser.
  return (
    isLocalDeployment()
    && typeof window.__TAURI__?.core?.invoke === "function"
  );
}

export function useDesktopBridge(): boolean {
  return useSyncExternalStore(subscribeDesktopBridge, readDesktopBridge, () => false);
}

/** Non-reactive form, for callers already inside an event handler. */
export function desktopBridgeAvailable(): boolean {
  return typeof window !== "undefined" && readDesktopBridge();
}

/**
 * Whether an app embedded in an iframe would still be signed in.
 *
 * On macOS it is not, and no cookie attribute can change that. `localhost` is
 * not in the Public Suffix List, so WebKit cannot derive a registrable domain
 * and treats every `*.lemma.localhost` host as its own site. An app iframed
 * from `<slug>.apps.lemma.localhost` into a workspace on
 * `app.lemma.localhost` is therefore third-party, and WebKit blocks its
 * storage outright: measured in a WKWebView harness, the server's `Set-Cookie`
 * is not stored, a `document.cookie` write is silently dropped and reads back
 * empty, a credentialed fetch to `/_lemma/users/me` answers 401, and
 * `document.hasStorageAccess()` is false. The app loads permanently signed out
 * and its SDK refreshes for ever trying to fix it.
 *
 * Top-level is fine — the same host in its own window gets the session, which
 * is why the answer is a window rather than a redesign.
 *
 * Derived rather than configured, from two things that are never stale.
 * `platform` is baked from `std::env::consts::OS` and cannot change for the
 * life of the process, unlike `mode` (see `readDesktopBridge` above for what
 * that staleness already cost). `location.hostname` is read at render, so this
 * corrects itself the moment the local hostnames move to a real registrable
 * domain — no flag to flip, no shell change, and the fallback path when that
 * domain cannot be resolved needs no coordination either.
 *
 * Chromium and WebView2 treat `*.localhost` as same-site, so a LAN browser, a
 * public link, and the Windows build all keep their iframes.
 */
export function crossSiteFramesCarryCookies(): boolean {
  if (typeof window === "undefined") return true;
  if (window.__LEMMA_DESKTOP__?.platform !== "macos") return true;
  return !window.location.hostname.endsWith(".localhost");
}
