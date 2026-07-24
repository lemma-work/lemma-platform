"use client";

import { useCallback, useEffect, useState } from "react";
import { buildApiUrl } from "@/components/auth/portal/auth/config";

type CapabilityHealth = {
  capabilities?: {
    ai_profile?: {
      status?: string;
    };
  };
};

declare global {
  interface Window {
    __TAURI__?: {
      core?: {
        invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
    };
  }
}

export function LocalAiSetupBanner() {
  const [needsSetup, setNeedsSetup] = useState(false);

  const refresh = useCallback(async () => {
    if (window.__LEMMA_DESKTOP__?.mode !== "local") {
      setNeedsSetup(false);
      return;
    }
    try {
      const response = await fetch(buildApiUrl("/health/capabilities"), {
        credentials: "include",
        cache: "no-store",
      });
      if (!response.ok) return;
      const health = (await response.json()) as CapabilityHealth;
      setNeedsSetup(
        health.capabilities?.ai_profile?.status === "needs_setup",
      );
    } catch {
      // Core readiness already has its own recovery UI. Do not add a second
      // warning when the capability probe is temporarily unavailable.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, 15_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  if (!needsSetup) return null;

  return (
    <aside className="sticky top-0 z-[70] flex min-h-12 items-center justify-between gap-4 border-b border-amber-300/60 bg-amber-50 px-5 py-2.5 text-sm text-amber-950 shadow-sm dark:border-amber-700/60 dark:bg-amber-950 dark:text-amber-50">
      <span>
        <strong>Configure an AI provider.</strong>{" "}
        Agents are unavailable until a provider validates; the rest of Lemma is ready.
      </span>
      <button
        type="button"
        className="shrink-0 rounded-md bg-amber-950 px-3 py-1.5 font-medium text-white hover:bg-amber-800 dark:bg-amber-100 dark:text-amber-950 dark:hover:bg-white"
        onClick={() => {
          void window.__TAURI__?.core?.invoke?.("open_control_center", {
            page: "ai",
          });
        }}
      >
        Configure AI
      </button>
    </aside>
  );
}
