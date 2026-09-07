"use client";

import QRCode from "react-qr-code";

import { Button } from "@/components/ui/button";
import { Clock, Copy, ExternalLink, MessageCircle } from "@/components/ui/icons";
import {
  useWhatsAppMobileVerification,
  useWhatsAppVerificationConfig,
} from "@/lib/identity/use-whatsapp-mobile-verification";

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
  const { data: config } = useWhatsAppVerificationConfig();
  const {
    transaction,
    starting,
    error,
    secondsRemaining,
    message,
    start,
    cancel,
    copyMessage,
  } = useWhatsAppMobileVerification({ onVerified });

  if (alreadyVerified || config?.available !== true) return null;

  if (!transaction) {
    return (
      <div className="space-y-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={!mobileNumber || !mobileNumberValid}
          loading={starting}
          loadingLabel="Preparing WhatsApp"
          onClick={() => void start(mobileNumber)}
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
            Send this message from the number you’re verifying
          </h3>
        </div>
        <span className="chip chip-sm chip-pill chip-muted">
          <Clock className="h-3 w-3" />
          {Math.floor(secondsRemaining / 60)}:{String(secondsRemaining % 60).padStart(2, "0")}
        </span>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-[minmax(0,1fr)_9.5rem]">
        <div className="space-y-3">
          <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
            <p className="type-eyebrow text-[var(--text-tertiary)]">Send to</p>
            <a
              href={transaction.whatsapp_url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 block w-fit text-lg font-semibold tabular-nums text-[var(--text-primary)] underline decoration-[var(--border-strong)] underline-offset-4 hover:decoration-[var(--text-primary)]"
            >
              {transaction.display_number}
            </a>
            <p className="mt-1 text-xs text-[var(--text-tertiary)]">
              Lemma&apos;s WhatsApp verification number
            </p>
          </div>

          <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
            <p className="type-eyebrow text-[var(--text-tertiary)]">Message to send</p>
            <code className="mt-2 block select-all break-all text-sm font-semibold tracking-wide text-[var(--text-primary)]">
              {message}
            </code>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => void copyMessage()}
            >
              <Copy className="mr-1.5 h-3.5 w-3.5" />
              Copy full message
            </Button>
          </div>
        </div>

        <div className="flex flex-col items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3 text-center">
          <div className="rounded-md bg-[var(--text-inverse)] p-2">
            <QRCode
              value={transaction.whatsapp_url}
              size={112}
              level="M"
              bgColor="var(--text-inverse)"
              fgColor="var(--text-primary)"
              aria-label="Scan to open the verification message in WhatsApp"
            />
          </div>
          <p className="mt-2 text-xs font-medium text-[var(--text-secondary)]">
            Scan with your phone
          </p>
        </div>
      </div>
      <p className="mt-3 text-xs leading-5 text-[var(--text-secondary)]">
        Send the message from the mobile number on your Lemma profile. The QR code opens
        WhatsApp on another device with the number and message already filled in.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" asChild type="button" size="sm">
          <a href={transaction.whatsapp_url} target="_blank" rel="noreferrer">
            Open WhatsApp
            <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
          </a>
        </Button>
        <Button type="button" variant="quiet" size="sm" onClick={cancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
