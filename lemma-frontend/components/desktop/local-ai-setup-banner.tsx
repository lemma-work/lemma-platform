"use client";

import { useCallback, useEffect, useState } from "react";
import { buildApiUrl } from "@/components/auth/portal/auth/config";
import { Button } from "@/components/ui/button";

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
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(refresh, 15_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  if (!needsSetup) return null;

  return (
    <aside className="state-surface-warning sticky top-0 z-[70] flex min-h-12 items-center justify-between gap-4 px-5 py-2.5 text-sm">
      <span>
        <strong>Configure an AI provider.</strong>{" "}
        Agents are unavailable until a provider validates; the rest of Lemma is ready.
      </span>
      <Button
        type="button"
        size="sm"
        className="shrink-0"
        onClick={() => {
          void window.__TAURI__?.core?.invoke?.("open_control_center", {
            page: "ai",
          });
        }}
      >
        Configure AI
      </Button>
    </aside>
  );
}
