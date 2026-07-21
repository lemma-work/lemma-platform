"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import {
  fetchAltchaEnabled,
  getAltchaProgress,
  subscribeAltchaProgress,
} from "@/components/auth/portal/auth/altcha";
import { AlertCircle, Loader2, ShieldCheck } from "@/components/ui/icons";

export function AuthProtectionNotice() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const progress = useSyncExternalStore(
    subscribeAltchaProgress,
    getAltchaProgress,
    getAltchaProgress,
  );

  useEffect(() => {
    let active = true;
    void fetchAltchaEnabled().then((enabled) => {
      if (active) setConfigured(enabled);
    });
    return () => {
      active = false;
    };
  }, []);

  const enabled = progress.enabled ?? configured;
  if (enabled !== true) return null;

  const isWorking = progress.phase === "checking" || progress.phase === "solving";
  const isError = progress.phase === "error";
  const message =
    progress.phase === "checking"
      ? "Preparing a private security check…"
      : progress.phase === "solving"
        ? "Completing a quick security check…"
        : progress.phase === "complete"
          ? "Private security check complete."
          : isError
            ? "Security check unavailable. Try submitting again."
            : "Protected by private proof-of-work—no tracking CAPTCHA.";
  const Icon = isWorking ? Loader2 : isError ? AlertCircle : ShieldCheck;

  return (
    <div
      className={`auth-protection-notice${isError ? " auth-protection-notice-error" : ""}`}
      role="status"
      aria-live="polite"
    >
      <Icon
        size={17}
        weight="duotone"
        className={isWorking ? "auth-protection-spinner" : undefined}
        aria-hidden="true"
      />
      <span>{message}</span>
    </div>
  );
}
