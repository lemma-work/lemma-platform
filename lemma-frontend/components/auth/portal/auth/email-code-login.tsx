"use client";

import { useEffect, useState, type FormEvent } from "react";

import { buildApiUrl } from "@/components/auth/portal/auth/config";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Challenge = { challenge_id: string; expires_at: string };

const COOLDOWN_MS = 60_000;
const GENERIC_FAILURE = "Unable to continue. Please try again.";

async function requestCode<T>(path: string, body: object): Promise<T> {
  const response = await fetch(buildApiUrl(`/auth/email-code/${path}`), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "st-auth-mode": "cookie" },
    body: JSON.stringify(body),
  });
  // A proxy in front of the API answers an outage in HTML, and a body that does
  // not parse would otherwise reach the person as a JSON syntax error.
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof result.detail === "string" ? result.detail : GENERIC_FAILURE);
  }
  return result as T;
}

function failureMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

export function EmailCodeLogin({
  onBack,
  onAuthenticated = () => window.location.reload(),
}: {
  onBack: () => void;
  onAuthenticated?: () => void;
}) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [nonce, setNonce] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sentAt, setSentAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const cooldown = Math.max(0, Math.ceil((sentAt + COOLDOWN_MS - now) / 1000));
  const expired = challenge !== null && Date.parse(challenge.expires_at) <= now;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  function codeSent(next: Challenge) {
    setChallenge(next);
    setSentAt(Date.now());
    setNow(Date.now());
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      // The nonce is the browser's half of the binding: the cookie the API set
      // alongside it has to match the one echoed here, so it is minted once and
      // kept for the whole exchange, including after "Change email".
      const binding = nonce ?? (await requestCode<{ nonce: string }>("browser", {})).nonce;
      setNonce(binding);
      if (challenge) {
        await requestCode("verify", {
          nonce: binding,
          challenge_id: challenge.challenge_id,
          code,
        });
        // Re-enter the existing authenticated navigation, including desktop and
        // CLI handoffs.
        onAuthenticated();
      } else {
        codeSent(
          await requestCode<Challenge>("start", { email: email.trim(), nonce: binding }),
        );
      }
    } catch (cause) {
      setError(failureMessage(cause, GENERIC_FAILURE));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!challenge || !nonce) return;
    setBusy(true);
    setError("");
    try {
      codeSent(
        await requestCode<Challenge>("resend", {
          nonce,
          challenge_id: challenge.challenge_id,
        }),
      );
      setCode("");
    } catch (cause) {
      setError(failureMessage(cause, "Unable to resend. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  function changeEmail() {
    setChallenge(null);
    setCode("");
    setError("");
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <h2>Continue with email code</h2>
      {challenge ? (
        <>
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
        </>
      ) : (
        <>
          <p className="helper-copy">
            Sign in or create your account with a code sent to your email.
          </p>
          <label htmlFor="email-login-address">Email address</label>
          <Input
            id="email-login-address"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            autoFocus
          />
        </>
      )}
      {error && (
        <p role="alert" className="status-inline status-inline-danger">
          {error}
        </p>
      )}
      <Button type="submit" disabled={busy || expired}>
        {busy ? "Please wait…" : challenge ? "Verify and continue" : "Send code"}
      </Button>
      {challenge && (
        <>
          <Button
            type="button"
            variant="secondary"
            onClick={() => void resend()}
            disabled={busy || cooldown > 0}
          >
            {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
          </Button>
          <Button type="button" variant="quiet" disabled={busy} onClick={changeEmail}>
            Change email
          </Button>
        </>
      )}
      <Button type="button" variant="quiet" disabled={busy} onClick={onBack}>
        Other sign-in options
      </Button>
    </form>
  );
}
