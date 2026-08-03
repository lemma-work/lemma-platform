"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { buildApiUrl } from "@/components/auth/portal/auth/config";

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
  return (
    window.__LEMMA_DESKTOP__?.mode === "local"
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
