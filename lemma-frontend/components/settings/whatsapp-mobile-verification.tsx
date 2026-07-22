"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { buildApiUrl } from "@/components/auth/portal/auth/config";
import { Button } from "@/components/ui/button";
import { Clock, Copy, ExternalLink, MessageCircle } from "@/components/ui/icons";

type VerificationConfig = {
  available: boolean;
  display_number?: string | null;
};

type VerificationTransaction = {
  transaction_id: string;
  code: string;
  whatsapp_url: string;
  display_number: string;
  expires_at: string;
};

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string; message?: string };
    return payload.detail || payload.message || "Mobile verification could not start.";
  } catch {
    return "Mobile verification could not start.";
  }
}

export function WhatsAppMobileVerification({
  mobileNumber,
  mobileNumberValid,
  alreadyVerified,
  onVerified,
}: {
  mobileNumber: string;
  mobileNumberValid: boolean;
  alreadyVerified: boolean;
  onVerified: () => Promise<unknown>;
}) {
  const [config, setConfig] = useState<VerificationConfig | null>(null);
  const [transaction, setTransaction] = useState<VerificationTransaction | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let active = true;
    void fetch(buildApiUrl("/auth/mobile-verification/whatsapp/config"), {
      credentials: "include",
    })
      .then((response) => (response.ok ? response.json() : { available: false }))
      .then((payload: VerificationConfig) => {
        if (active) setConfig(payload);
      })
      .catch(() => {
        if (active) setConfig({ available: false });
      });
    return () => {
      active = false;
    };
  }, []);

  const expiresAt = transaction ? Date.parse(transaction.expires_at) : 0;
  const secondsRemaining = transaction
    ? Math.max(0, Math.ceil((expiresAt - now) / 1000))
    : 0;

  const expireTransaction = useCallback(() => {
    setTransaction(null);
    setError("That code expired. Start again for a fresh code.");
  }, []);

  useEffect(() => {
    if (!transaction) return;
    const timer = window.setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= expiresAt) expireTransaction();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [expireTransaction, expiresAt, transaction]);

  useEffect(() => {
    if (!transaction) return;
    let active = true;
    let poll: number | null = null;

    const check = async () => {
      if (!active || document.visibilityState !== "visible") return;
      try {
        const response = await fetch(
          buildApiUrl(
            `/auth/mobile-verification/whatsapp/status/${encodeURIComponent(transaction.transaction_id)}`,
          ),
          { credentials: "include" },
        );
        if (!response.ok) return;
        const payload = (await response.json()) as {
          status: "PENDING" | "VERIFIED" | "EXPIRED";
        };
        if (!active) return;
        if (payload.status === "VERIFIED") {
          setTransaction(null);
          await onVerified();
          toast.success("Mobile number verified");
        } else if (payload.status === "EXPIRED") {
          expireTransaction();
        }
      } catch {
        // Polling is best effort; the next visible interval retries.
      }
    };
    const beginPolling = () => {
      if (poll !== null) window.clearInterval(poll);
      if (document.visibilityState !== "visible") return;
      void check();
      poll = window.setInterval(() => void check(), 2000);
    };
    const visibilityChanged = () => beginPolling();
    beginPolling();
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => {
      active = false;
      if (poll !== null) window.clearInterval(poll);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [expireTransaction, onVerified, transaction]);

  const manualMessage = useMemo(
    () => (transaction ? `LEMMA VERIFY ${transaction.code}` : ""),
    [transaction],
  );

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const response = await fetch(
        buildApiUrl("/auth/mobile-verification/whatsapp/start"),
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mobile_number: mobileNumber }),
        },
      );
      if (!response.ok) throw new Error(await responseError(response));
      const created = (await response.json()) as VerificationTransaction;
      setTransaction(created);
      setNow(Date.now());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Mobile verification could not start.");
    } finally {
      setStarting(false);
    }
  };

  if (alreadyVerified || config?.available !== true) return null;

  if (!transaction) {
    return (
      <div className="space-y-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!mobileNumber || !mobileNumberValid}
          loading={starting}
          loadingLabel="Preparing WhatsApp"
          onClick={() => void start()}
        >
          <MessageCircle className="mr-1.5 h-3.5 w-3.5" />
          Verify mobile with WhatsApp
        </Button>
        {error ? <p className="text-xs text-[var(--state-error)]" role="alert">{error}</p> : null}
      </div>
    );
  }

  return (
    <section className="surface-panel surface-panel-muted p-4" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="type-eyebrow text-[var(--text-tertiary)]">Verify in WhatsApp</p>
          <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
            Send this code from the number you’re verifying
          </h3>
        </div>
        <span className="chip chip-sm chip-pill chip-muted">
          <Clock className="h-3 w-3" />
          {Math.floor(secondsRemaining / 60)}:{String(secondsRemaining % 60).padStart(2, "0")}
        </span>
      </div>
      <div className="mt-4 flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-2.5">
        <code className="min-w-0 flex-1 select-all text-center text-base font-semibold tracking-widest text-[var(--text-primary)]">
          {transaction.code}
        </code>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Copy verification message"
          onClick={() => {
            void navigator.clipboard.writeText(manualMessage);
            toast.success("Verification message copied");
          }}
        >
          <Copy className="h-4 w-4" />
        </Button>
      </div>
      <p className="mt-3 text-xs leading-5 text-[var(--text-secondary)]">
        Open WhatsApp and send <strong>{manualMessage}</strong> to {transaction.display_number}.
        The number you send it from must match your profile number.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button asChild type="button" size="sm">
          <a href={transaction.whatsapp_url} target="_blank" rel="noreferrer">
            Open WhatsApp
            <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
          </a>
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setTransaction(null)}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
