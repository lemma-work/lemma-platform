"use client";

import { useState, type FormEvent } from "react";

import {
  emailCodeErrorMessage,
  mintNonce,
  startChallenge,
  type Challenge,
} from "@/components/auth/portal/auth/email-code-client";
import { EmailCodeStep } from "@/components/auth/portal/auth/email-code-step";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function EmailCodeLogin({
  onBack,
  onAuthenticated = () => window.location.reload(),
}: {
  onBack: () => void;
  onAuthenticated?: () => void;
}) {
  const [email, setEmail] = useState("");
  const [nonce, setNonce] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      // The nonce is the browser's half of the binding: the cookie the API set
      // alongside it has to match the one echoed here, so it is minted once and
      // kept for the whole exchange, including after "Change email".
      const binding = nonce ?? (await mintNonce());
      setNonce(binding);
      setChallenge(await startChallenge({ email: email.trim(), nonce: binding }));
    } catch (cause) {
      setError(emailCodeErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const otherOptions = (
    <Button
      type="button"
      variant="link"
      className="auth-text-button"
      disabled={busy}
      onClick={onBack}
    >
      Other sign-in options
    </Button>
  );

  if (challenge && nonce) {
    return (
      <EmailCodeStep
        email={email}
        nonce={nonce}
        initialChallenge={challenge}
        onVerified={onAuthenticated}
        onChangeEmail={() => {
          setChallenge(null);
          setError("");
        }}
      >
        {otherOptions}
      </EmailCodeStep>
    );
  }

  return (
    <form onSubmit={submit} className="auth-owned-form" noValidate>
      <div className="auth-owned-heading">
        <h2 className="auth-owned-title">Continue with email code</h2>
        <p className="auth-owned-subtitle">
          Sign in or create your account with a code sent to your email.
        </p>
      </div>
      <label className="auth-owned-field">
        <span>Email</span>
        <Input
          id="email-login-address"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
          autoFocus
        />
      </label>
      {error && (
        <p role="alert" className="auth-owned-error">
          {error}
        </p>
      )}
      <Button
        variant="primary"
        type="submit"
        className="primary-button auth-portal-session-button"
        disabled={busy}
      >
        {busy ? "Please wait…" : "Send code"}
      </Button>
      {otherOptions}
    </form>
  );
}
