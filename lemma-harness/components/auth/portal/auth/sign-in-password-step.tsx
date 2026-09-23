"use client";

import type { FormEvent, ReactNode } from "react";

import { authConfig } from "@/components/auth/portal/auth/config";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function resetPasswordUrl(): string {
  const base =
    authConfig.websiteBasePath === "/" ? "" : authConfig.websiteBasePath;
  return new URL(`${base}/reset-password`, authConfig.websiteUrl).toString();
}

/**
 * The second half of an identifier-first sign-in, for an address that has a
 * password.
 *
 * Its own component because it is its own view: the step before it and the step
 * after it each carry their own primary action, and three of those in one file
 * is the shape `design.md` §8 asks you to look twice at -- correctly, even
 * though only one is ever on screen.
 */
export function SignInPasswordStep({
  email,
  password,
  onPasswordChange,
  busy,
  alert,
  fallback,
  onSubmit,
  onBack,
  onUseCode,
}: {
  email: string;
  password: string;
  onPasswordChange: (value: string) => void;
  busy: boolean;
  alert: ReactNode;
  /** Whether to offer a code, because the refusal said this account has no password. */
  fallback: boolean;
  onSubmit: (event: FormEvent) => void;
  onBack: () => void;
  onUseCode: () => void;
}) {
  return (
    <form onSubmit={onSubmit} className="auth-owned-form" noValidate>
      <div className="auth-owned-heading">
        <h2 className="auth-owned-title">Enter your password</h2>
        <p className="auth-owned-subtitle">
          Signing in as {email}.{" "}
          <Button
            type="button"
            variant="link"
            size="xs"
            disabled={busy}
            onClick={onBack}
          >
            Use a different email
          </Button>
        </p>
      </div>
      {/*
        Visible and inside this form on purpose. A password manager fills a
        password by finding the username beside it, and a split identifier step
        hands it a form with no username at all -- hidden inputs are widely
        ignored, so the field has to be real and readable.
      */}
      <label className="auth-owned-field auth-identity-chip">
        <span>Email</span>
        <Input
          id="sign-in-identity"
          type="email"
          autoComplete="username"
          value={email}
          tabIndex={-1}
          readOnly
        />
      </label>
      <label className="auth-owned-field">
        <span>Password</span>
        <Input
          id="sign-in-password"
          type="password"
          autoComplete="current-password"
          placeholder="Password"
          value={password}
          onChange={(event) => onPasswordChange(event.target.value)}
          required
          autoFocus
        />
      </label>
      {alert}
      <Button
        variant="primary"
        type="submit"
        className="primary-button auth-portal-session-button"
        disabled={busy}
      >
        {busy ? "Please wait…" : "Sign in"}
      </Button>
      {fallback && (
        <Button
          type="button"
          variant="secondary"
          disabled={busy}
          onClick={onUseCode}
        >
          Email me a code instead
        </Button>
      )}
      <Button
        type="button"
        variant="link"
        className="auth-text-button"
        disabled={busy}
        onClick={() => window.location.assign(resetPasswordUrl())}
      >
        Forgot password?
      </Button>
    </form>
  );
}
