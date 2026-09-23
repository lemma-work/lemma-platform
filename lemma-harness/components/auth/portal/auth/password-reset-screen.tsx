"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import EmailPassword from "supertokens-auth-react/recipe/emailpassword";

import { authConfig } from "@/components/auth/portal/auth/config";
import {
  classifyPasswordResetFailure,
  passwordResetCooldownSeconds,
  passwordResetErrorMessage,
  passwordResetTokenFromSearch,
  runPasswordResetRequest,
  runPasswordResetSubmit,
  validateNewPasswords,
} from "@/components/auth/portal/auth/password-reset-controller";
import { StatusPanel } from "@/components/auth/portal/auth-portal-chrome";
import { AlertCircle, CheckCircle2, KeyRound, Mail } from "@/components/ui/icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type ResetPhase =
  | "request"
  | "sending"
  | "sent"
  | "new-password"
  | "submitting"
  | "success"
  | "invalid"
  | "rate-limited"
  | "error";

function signInUrl(): string {
  return new URL(authConfig.websiteBasePath, authConfig.websiteUrl).toString();
}

function resetRequestUrl(): string {
  const base = authConfig.websiteBasePath === "/" ? "" : authConfig.websiteBasePath;
  return new URL(`${base}/reset-password`, authConfig.websiteUrl).toString();
}

function ResetIcon({ phase }: { phase: ResetPhase }) {
  const Icon =
    phase === "success"
      ? CheckCircle2
      : phase === "invalid" || phase === "error" || phase === "rate-limited"
        ? AlertCircle
        : phase === "sent"
          ? Mail
          : KeyRound;
  return (
    <span className={`verification-icon password-reset-icon-${phase}`} aria-hidden="true">
      <Icon weight="regular" />
    </span>
  );
}

export function PasswordResetScreen() {
  const token = passwordResetTokenFromSearch(window.location.search);
  const [phase, setPhase] = useState<ResetPhase>(token ? "new-password" : "request");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastSentAt, setLastSentAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const cooldown = passwordResetCooldownSeconds(lastSentAt, now);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  const requestReset = useCallback(async () => {
    const normalizedEmail = email.trim();
    if (!normalizedEmail) {
      setFieldError("Enter your email address.");
      return;
    }
    setFieldError(null);
    setErrorMessage(null);
    setPhase("sending");
    try {
      const result = await runPasswordResetRequest(() =>
        EmailPassword.sendPasswordResetEmail({
          formFields: [{ id: "email", value: normalizedEmail }],
        }),
      );
      if (result.status === "field-error") {
        setFieldError(result.message);
        setPhase("request");
        return;
      }
      const sentAt = Date.now();
      setLastSentAt(sentAt);
      setNow(sentAt);
      setPhase("sent");
    } catch (error) {
      const failure = classifyPasswordResetFailure(error);
      setErrorMessage(passwordResetErrorMessage(error));
      setPhase(failure);
    }
  }, [email]);

  const submitPassword = useCallback(async () => {
    const validationError = validateNewPasswords(password, confirmation);
    if (validationError) {
      setFieldError(validationError);
      return;
    }
    setFieldError(null);
    setErrorMessage(null);
    setPhase("submitting");
    try {
      const result = await runPasswordResetSubmit(() =>
        EmailPassword.submitNewPassword({
          formFields: [{ id: "password", value: password }],
        }),
      );
      if (result.status === "invalid-token") {
        setPhase("invalid");
        return;
      }
      if (result.status === "field-error") {
        setFieldError(result.message);
        setPhase("new-password");
        return;
      }
      setPhase("success");
    } catch (error) {
      const failure = classifyPasswordResetFailure(error);
      setErrorMessage(passwordResetErrorMessage(error));
      setPhase(failure);
    }
  }, [confirmation, password]);

  const submitRequestForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void requestReset();
  };
  const submitPasswordForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitPassword();
  };

  if (phase === "sending" || phase === "submitting") {
    return (
      <StatusPanel
        eyebrow="Account recovery"
        title={phase === "sending" ? "Sending a secure link…" : "Securing your new password…"}
        description="This usually takes only a moment."
      >
        <div className="verification-state" role="status" aria-live="polite">
          <div className="spinner" aria-hidden="true" />
        </div>
      </StatusPanel>
    );
  }

  if (phase === "sent") {
    return (
      <StatusPanel
        eyebrow="Check your inbox"
        title="Your reset link is on its way."
        description="If an account exists for that email, you’ll receive a secure password reset link shortly."
      >
        <div className="verification-state" role="status" aria-live="polite">
          <ResetIcon phase={phase} />
          <p className="verification-message">Check spam or junk if it doesn’t arrive.</p>
        </div>
        <div className="verification-actions">
          <Button
            type="button"
            variant="secondary"
            className="secondary-button auth-portal-session-button"
            disabled={cooldown > 0}
            onClick={() => void requestReset()}
          >
            {cooldown > 0 ? `Resend available in ${cooldown}s` : "Resend reset email"}
          </Button>
          <Button
            type="button"
            variant="link"
            className="auth-text-button"
            onClick={() => {
              setFieldError(null);
              setPhase("request");
            }}
          >
            Change email address
          </Button>
        </div>
      </StatusPanel>
    );
  }

  if (phase === "success") {
    return (
      <StatusPanel
        eyebrow="Password updated"
        title="Your new password is ready."
        description="Sign in with your new password to continue to Lemma."
      >
        <div className="verification-state" role="status" aria-live="polite">
          <ResetIcon phase={phase} />
        </div>
        <Button variant="secondary"
          type="button"
          className="primary-button auth-portal-session-button"
          onClick={() => window.location.replace(signInUrl())}
        >
          Sign in
        </Button>
      </StatusPanel>
    );
  }

  if (phase === "invalid") {
    return (
      <StatusPanel
        eyebrow="Link expired"
        title="This reset link no longer works."
        description="Password reset links are time-limited and single-use. Request a fresh one to continue."
        tone="danger"
      >
        <div className="verification-state" role="alert">
          <ResetIcon phase={phase} />
        </div>
        <Button variant="secondary"
          type="button"
          className="primary-button auth-portal-session-button"
          onClick={() => {
            window.location.replace(resetRequestUrl());
          }}
        >
          Request a new link
        </Button>
      </StatusPanel>
    );
  }

  if (phase === "rate-limited" || phase === "error") {
    return (
      <StatusPanel
        eyebrow={phase === "rate-limited" ? "Please wait a moment" : "Couldn’t continue"}
        title={phase === "rate-limited" ? "Too many reset attempts." : "Let’s try that again."}
        description={
          errorMessage ||
          (phase === "rate-limited"
            ? "Wait a few minutes before requesting another reset."
            : "Your account and password are unchanged.")
        }
        tone="danger"
      >
        <div className="verification-state" role="alert">
          <ResetIcon phase={phase} />
        </div>
        <div className="button-row">
          <Button variant="secondary"
            type="button"
            className="primary-button auth-portal-session-button"
            onClick={() => {
              setErrorMessage(null);
              setPhase(token ? "new-password" : lastSentAt ? "sent" : "request");
            }}
          >
            Try again
          </Button>
          <Button type="button" variant="link" className="auth-text-button" onClick={() => window.location.replace(signInUrl())}>
            Return to sign in
          </Button>
        </div>
      </StatusPanel>
    );
  }

  if (phase === "new-password") {
    return (
      <StatusPanel
        eyebrow="Choose a new password"
        title="Secure your Lemma account."
        description="Use a password you don’t use on another service."
      >
        <form className="auth-owned-form" onSubmit={submitPasswordForm} noValidate>
          <label className="auth-owned-field">
            <span>New password</span>
            <Input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoFocus
            />
          </label>
          <label className="auth-owned-field">
            <span>Confirm new password</span>
            <Input
              type="password"
              autoComplete="new-password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          {fieldError ? <p className="auth-owned-error" role="alert">{fieldError}</p> : null}
          <Button variant="primary" type="submit" className="primary-button auth-portal-session-button">
            Update password
          </Button>
        </form>
      </StatusPanel>
    );
  }

  return (
    <StatusPanel
      eyebrow="Account recovery"
      title="Reset your password."
      description="Enter your Lemma email. We’ll send a secure, time-limited reset link."
    >
      <form className="auth-owned-form" onSubmit={submitRequestForm} noValidate>
        <label className="auth-owned-field">
          <span>Email</span>
          <Input
            type="email"
            inputMode="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@company.com"
            autoFocus
          />
        </label>
        {fieldError ? <p className="auth-owned-error" role="alert">{fieldError}</p> : null}
        <Button variant="primary" type="submit" className="primary-button auth-portal-session-button">
          Send reset link
        </Button>
        <Button type="button" variant="link" className="auth-text-button" onClick={() => window.location.replace(signInUrl())}>
          Return to sign in
        </Button>
      </form>
    </StatusPanel>
  );
}
