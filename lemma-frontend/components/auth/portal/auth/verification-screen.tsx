"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useRef, useState } from "react";
import EmailVerification from "supertokens-auth-react/recipe/emailverification";
import Session from "supertokens-auth-react/recipe/session";

import {
  clearStoredRedirectUri,
  consumeStoredRedirectUri,
  getDefaultPostAuthRedirect,
} from "@/components/auth/portal/auth/redirects";
import { getStoredDesktopRequestId } from "@/components/auth/portal/auth/desktop";
import { authConfig } from "@/components/auth/portal/auth/config";
import {
  runVerificationAttempt,
  runVerificationSend,
  refreshVerifiedSession,
  signOutForDifferentAccount,
  verificationCooldownSeconds,
  verificationDestination,
  verificationTokenFromSearch,
} from "@/components/auth/portal/auth/verification-controller";
import { StatusPanel } from "@/components/auth/portal/auth-portal-chrome";
import { AlertCircle, CheckCircle2, Mail, RefreshCw } from "@/components/ui/icons";
import { Button } from "@/components/ui/button";

type VerificationPhase =
  | "sending"
  | "inbox"
  | "verifying"
  | "verified"
  | "invalid"
  | "error"
  | "sign-in-required";

function destinationAfterVerification(): string {
  const desktopRequestId = getStoredDesktopRequestId();
  return verificationDestination({
    desktopRequestId,
    authLandingUrl: new URL(
      authConfig.websiteBasePath,
      authConfig.websiteUrl,
    ).toString(),
    redirectUri: desktopRequestId ? null : consumeStoredRedirectUri(),
    defaultRedirect: getDefaultPostAuthRedirect(),
  });
}

function VerificationIcon({ phase }: { phase: VerificationPhase }) {
  const Icon =
    phase === "verified"
      ? CheckCircle2
      : phase === "invalid" || phase === "error"
        ? AlertCircle
        : phase === "verifying" || phase === "sending"
          ? RefreshCw
          : Mail;
  return (
    <span
      className={`verification-icon verification-icon-${phase}`}
      aria-hidden="true"
    >
      <Icon weight="regular" />
    </span>
  );
}

export function VerificationScreen({
  doesSessionExist,
}: {
  doesSessionExist: boolean;
}) {
  const token = verificationTokenFromSearch(window.location.search);
  const [phase, setPhase] = useState<VerificationPhase>(
    token ? "verifying" : doesSessionExist ? "sending" : "sign-in-required",
  );
  const [lastSentAt, setLastSentAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [resendMessage, setResendMessage] = useState<string | null>(null);
  const initialAttemptStarted = useRef(false);
  const cooldownSeconds = verificationCooldownSeconds(lastSentAt, now);

  const finishVerification = useCallback(async () => {
    await refreshVerifiedSession(() => Session.attemptRefreshingSession());
    setPhase("verified");
  }, []);

  const sendEmail = useCallback(async () => {
    if (!doesSessionExist) {
      setPhase("sign-in-required");
      return;
    }
    setResendMessage(null);
    const result = await runVerificationSend(() =>
      EmailVerification.sendVerificationEmail(),
    );
    if (result === "already-verified") {
      await finishVerification();
      return;
    }
    if (result === "error") {
      setPhase("error");
      return;
    }
    const sentAt = Date.now();
    setLastSentAt(sentAt);
    setNow(sentAt);
    setResendMessage("A fresh verification email is on its way.");
    setPhase("inbox");
  }, [doesSessionExist, finishVerification]);

  const verifyToken = useCallback(async () => {
    setPhase("verifying");
    const result = await runVerificationAttempt(() => EmailVerification.verifyEmail());
    if (result === "verified") {
      await finishVerification();
    } else {
      setPhase(result);
    }
  }, [finishVerification]);

  useEffect(() => {
    if (initialAttemptStarted.current) return;
    initialAttemptStarted.current = true;
    if (token) {
      void verifyToken();
      return;
    }
    if (!doesSessionExist) {
      setPhase("sign-in-required");
      return;
    }
    void EmailVerification.isEmailVerified()
      .then(async ({ isVerified }) => {
        if (isVerified) {
          await finishVerification();
          return;
        }
        await sendEmail();
      })
      .catch(() => setPhase("error"));
  }, [doesSessionExist, finishVerification, sendEmail, token, verifyToken]);

  useEffect(() => {
    if (cooldownSeconds <= 0) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [cooldownSeconds]);

  const switchAccount = async () => {
    await signOutForDifferentAccount(
      () => Session.signOut(),
      clearStoredRedirectUri,
    );
    window.location.replace(
      new URL(authConfig.websiteBasePath, authConfig.websiteUrl).toString(),
    );
  };

  if (phase === "sending" || phase === "verifying") {
    const verifying = phase === "verifying";
    return (
      <StatusPanel
        eyebrow="Account security"
        title={verifying ? "Verifying your email…" : "Sending your email…"}
        description={
          verifying
            ? "We’re checking this secure verification link."
            : "We’re preparing a secure verification link for your Lemma account."
        }
      >
        <div className="verification-state" role="status" aria-live="polite">
          <VerificationIcon phase={phase} />
        </div>
      </StatusPanel>
    );
  }

  if (phase === "verified") {
    return (
      <StatusPanel
        eyebrow="Email verified"
        title="Your account is ready."
        description="Your email is confirmed and your Lemma session is secured."
      >
        <div className="verification-state" role="status" aria-live="polite">
          <VerificationIcon phase={phase} />
        </div>
        <button
          type="button"
          className="primary-button auth-portal-session-button"
          onClick={() =>
            window.location.replace(
              doesSessionExist
                ? destinationAfterVerification()
                : new URL(authConfig.websiteBasePath, authConfig.websiteUrl).toString(),
            )
          }
        >
          {doesSessionExist ? "Continue to Lemma" : "Return to sign in"}
        </button>
      </StatusPanel>
    );
  }

  if (phase === "sign-in-required") {
    return (
      <StatusPanel
        eyebrow="Sign in required"
        title="Sign in to request a new link."
        description="This verification page is no longer attached to an active Lemma session."
      >
        <div className="verification-state">
          <VerificationIcon phase={phase} />
        </div>
        <button
          type="button"
          className="primary-button auth-portal-session-button"
          onClick={() =>
            window.location.replace(
              new URL(authConfig.websiteBasePath, authConfig.websiteUrl).toString(),
            )
          }
        >
          Return to sign in
        </button>
      </StatusPanel>
    );
  }

  if (phase === "invalid") {
    return (
      <StatusPanel
        eyebrow="Link expired"
        title="This verification link no longer works."
        description="Verification links are single-use. Send a fresh one and try again."
        tone="danger"
      >
        <div className="verification-state" role="alert">
          <VerificationIcon phase={phase} />
        </div>
        <button
          type="button"
          className="primary-button auth-portal-session-button"
          onClick={() => void sendEmail()}
        >
          Send a new link
        </button>
      </StatusPanel>
    );
  }

  if (phase === "error") {
    return (
      <StatusPanel
        eyebrow="Couldn’t complete verification"
        title="Let’s try that again."
        description="We couldn’t reach the verification service. Your link and account are unchanged."
        tone="danger"
      >
        <div className="verification-state" role="alert">
          <VerificationIcon phase={phase} />
        </div>
        <div className="button-row">
          <button
            type="button"
            className="primary-button auth-portal-session-button"
            onClick={() => void (token ? verifyToken() : sendEmail())}
          >
            Try again
          </button>
          {doesSessionExist ? (
            <Button
              type="button"
              variant="link"
              className="auth-text-button"
              onClick={() => void switchAccount()}
            >
              Use a different account
            </Button>
          ) : null}
        </div>
      </StatusPanel>
    );
  }

  return (
    <StatusPanel
      eyebrow="Check your inbox"
      title="Verify your email."
      description="Open the verification link we sent to finish securing your Lemma account."
    >
      <div className="verification-state" role="status" aria-live="polite">
        <VerificationIcon phase={phase} />
        {resendMessage ? <p className="verification-message">{resendMessage}</p> : null}
      </div>
      <div className="verification-actions">
        <button
          type="button"
          className="secondary-button auth-portal-session-button"
          disabled={cooldownSeconds > 0}
          onClick={() => void sendEmail()}
        >
          {cooldownSeconds > 0
            ? `Resend available in ${cooldownSeconds}s`
            : "Resend verification email"}
        </button>
        <Button
          type="button"
          variant="link"
          className="auth-text-button"
          onClick={() => void switchAccount()}
        >
          Use a different account
        </Button>
      </div>
    </StatusPanel>
  );
}
