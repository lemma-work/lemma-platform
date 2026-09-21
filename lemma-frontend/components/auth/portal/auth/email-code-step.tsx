"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  emailCodeErrorMessage,
  resendChallenge,
  verifyChallenge,
  type Challenge,
} from "@/components/auth/portal/auth/email-code-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const COOLDOWN_MS = 60_000;

/**
 * Entering the six digits, once something has already sent them.
 *
 * Shared rather than duplicated because two flows reach this same point from
 * different directions: `EmailCodeLogin` gets here through `/start` after
 * asking for an address, and `SignInScreen` gets here because `/continue`
 * answered `code` and sent one on the way. Both arrive holding a challenge that
 * was created a moment ago, which is why the resend cooldown starts at mount
 * rather than waiting to be told — for both callers, "just now" is the truth.
 */
export function EmailCodeStep({
  email,
  nonce,
  initialChallenge,
  onVerified,
  onChangeEmail,
  heading = "Continue with email code",
  changeEmailLabel = "Change email",
  children,
}: {
  email: string;
  nonce: string;
  initialChallenge: Challenge;
  onVerified: () => void;
  onChangeEmail: () => void;
  heading?: string;
  changeEmailLabel?: string;
  children?: ReactNode;
}) {
  const [challenge, setChallenge] = useState<Challenge>(initialChallenge);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sentAt, setSentAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const cooldown = Math.max(0, Math.ceil((sentAt + COOLDOWN_MS - now) / 1000));
  const expired = Date.parse(challenge.expires_at) <= now;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await verifyChallenge({
        nonce,
        challengeId: challenge.challenge_id,
        code,
      });
      // Re-enter the existing authenticated navigation, including desktop and
      // CLI handoffs.
      onVerified();
    } catch (cause) {
      setError(emailCodeErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    setError("");
    try {
      const next = await resendChallenge({
        nonce,
        challengeId: challenge.challenge_id,
      });
      setChallenge(next);
      setSentAt(Date.now());
      setNow(Date.now());
      setCode("");
    } catch (cause) {
      setError(emailCodeErrorMessage(cause, "Unable to resend. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <h2>{heading}</h2>
      <p className="helper-copy">
        Enter the six-digit code sent to {email}. You have three attempts.
      </p>
      <label htmlFor="email-login-code">Verification code</label>
      <Input
        id="email-login-code"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]{6}"
        maxLength={6}
        required
        autoFocus
      />
      {expired && <p role="status">This code expired. Request a new code below.</p>}
      {error && (
        <p role="alert" className="status-inline status-inline-danger">
          {error}
        </p>
      )}
      <Button type="submit" disabled={busy || expired}>
        {busy ? "Please wait…" : "Verify and continue"}
      </Button>
      <Button
        type="button"
        variant="secondary"
        onClick={() => void resend()}
        disabled={busy || cooldown > 0}
      >
        {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
      </Button>
      <Button
        type="button"
        variant="quiet"
        disabled={busy}
        onClick={onChangeEmail}
      >
        {changeEmailLabel}
      </Button>
      {children}
    </form>
  );
}
