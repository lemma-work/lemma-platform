"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import EmailPassword from "supertokens-auth-react/recipe/emailpassword";
import ThirdParty from "supertokens-auth-react/recipe/thirdparty";

import { authConfig } from "@/components/auth/portal/auth/config";
import {
  continueWithEmail,
  emailCodeErrorMessage,
  mintNonce,
} from "@/components/auth/portal/auth/email-code-client";
import { EmailCodeStep } from "@/components/auth/portal/auth/email-code-step";
import {
  applyContinueResult,
  applyPasswordRejection,
  beginPasswordSubmit,
  beginResolve,
  changeEmail,
  continueRequest,
  failHandoff,
  failResolve,
  initialSignInState,
  runPasswordSignIn,
  validateIdentifier,
  type PasswordSignInResult,
  type SignInState,
} from "@/components/auth/portal/auth/sign-in-controller";
import type {
  ThirdPartyId,
  ThirdPartyProvider,
} from "@/components/auth/portal/auth/supertokens";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function resetPasswordUrl(): string {
  const base =
    authConfig.websiteBasePath === "/" ? "" : authConfig.websiteBasePath;
  return new URL(`${base}/reset-password`, authConfig.websiteUrl).toString();
}

/**
 * One email box, and whatever that address actually needs.
 *
 * The screen exists because an account made through WhatsApp has exactly one
 * login method and it is passwordless -- so a password field, "forgot
 * password" and Continue with Google are all refused for it, and the only door
 * that opens is a code. Asking the server which door to show, instead of
 * showing all of them and letting people find out by being turned away, is the
 * whole feature.
 *
 * `signIn` and `redirectToProvider` are injectable so a test can drive this
 * without `ensureSuperTokensInit()`. The defaults are the real singleton calls,
 * which is what earns the `override.functions.signIn` wrapper and the
 * `signin-risk` altcha proof the `preAPIHook` attaches -- a direct call gets
 * both, exactly as the prebuilt form does.
 */
export function SignInScreen({
  providers,
  onSignUp,
  onAuthenticated = () => window.location.reload(),
  telegram = null,
  signIn = (email, password) =>
    EmailPassword.signIn({
      formFields: [
        { id: "email", value: email },
        { id: "password", value: password },
      ],
    }) as Promise<PasswordSignInResult>,
  redirectToProvider = (thirdPartyId) =>
    ThirdParty.redirectToThirdPartyLogin({ thirdPartyId }),
}: {
  providers: readonly ThirdPartyProvider[];
  onSignUp: () => void;
  onAuthenticated?: () => void;
  telegram?: ReactNode;
  signIn?: (email: string, password: string) => Promise<PasswordSignInResult>;
  redirectToProvider?: (
    thirdPartyId: ThirdPartyId,
  ) => Promise<{ status: "OK" | "ERROR" }>;
}) {
  const [state, setState] = useState<SignInState>(initialSignInState);
  const [typed, setTyped] = useState("");
  const [password, setPassword] = useState("");
  const busy = state.step === "resolving" || state.step === "authenticating";

  useEffect(() => {
    if (state.step !== "handoff" || !state.provider) return;
    const provider = state.provider;
    let cancelled = false;
    void redirectToProvider(provider.id)
      .then((result) => {
        // "OK" means the browser is already navigating away; there is nothing
        // to render for it and nothing to undo.
        if (result.status === "OK" || cancelled) return;
        setState((current) =>
          failHandoff(current, `Couldn’t reach ${provider.name}. Try again.`),
        );
      })
      .catch(() => {
        if (!cancelled) {
          setState((current) =>
            failHandoff(current, `Couldn’t reach ${provider.name}. Try again.`),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [state.step, state.provider, redirectToProvider]);

  async function resolve(event?: FormEvent) {
    event?.preventDefault();
    const invalid = validateIdentifier(typed);
    if (invalid) {
      setState((current) => failResolve(current, invalid));
      return;
    }
    const pending = beginResolve(state, typed);
    setState(pending);
    try {
      const binding = pending.nonce ?? (await mintNonce());
      const result = await continueWithEmail(
        continueRequest(pending, typed, binding),
      );
      setState((current) =>
        applyContinueResult(current, binding, result, providers),
      );
    } catch (cause) {
      setState((current) => failResolve(current, emailCodeErrorMessage(cause)));
    }
  }

  async function submitPassword(event: FormEvent) {
    event.preventDefault();
    setState(beginPasswordSubmit(state));
    const outcome = await runPasswordSignIn(() =>
      signIn(state.email, password),
    );
    if (outcome.outcome === "authenticated") {
      onAuthenticated();
      return;
    }
    setPassword("");
    setState((current) => applyPasswordRejection(current, outcome));
  }

  function back() {
    setPassword("");
    setTyped(state.email);
    setState((current) => changeEmail(current));
  }

  const alert = state.error ? (
    <p role="alert" className="status-inline status-inline-danger">
      {state.error}
    </p>
  ) : null;

  if (state.step === "code" && state.challenge && state.nonce) {
    return (
      <EmailCodeStep
        email={state.email}
        nonce={state.nonce}
        initialChallenge={state.challenge}
        onVerified={onAuthenticated}
        onChangeEmail={back}
        heading="Check your email"
      />
    );
  }

  if (state.step === "handoff" && state.provider) {
    return (
      <div className="flex flex-col gap-4">
        <h2>Taking you to {state.provider.name}…</h2>
        <p className="helper-copy">
          This email signs in with {state.provider.name}.
        </p>
      </div>
    );
  }

  if (state.step === "password" || state.step === "authenticating") {
    return (
      <form onSubmit={submitPassword} className="flex flex-col gap-4">
        <h2>Enter your password</h2>
        {/*
          Visible and inside this form on purpose. A password manager fills a
          password by finding the username beside it, and a split identifier
          step hands it a form with no username at all -- hidden inputs are
          widely ignored, so the field has to be real and readable.
        */}
        <label htmlFor="sign-in-identity">Email address</label>
        <Input
          id="sign-in-identity"
          type="email"
          autoComplete="username"
          value={state.email}
          readOnly
        />
        <label htmlFor="sign-in-password">Password</label>
        <Input
          id="sign-in-password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          autoFocus
        />
        {alert}
        <Button type="submit" disabled={busy}>
          {busy ? "Please wait…" : "Sign in"}
        </Button>
        {state.passwordFallback && (
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() => void resolve()}
          >
            Email me a code instead
          </Button>
        )}
        <Button
          type="button"
          variant="link"
          disabled={busy}
          onClick={() => window.location.assign(resetPasswordUrl())}
        >
          Forgot password?
        </Button>
        <Button type="button" variant="quiet" disabled={busy} onClick={back}>
          Change email
        </Button>
      </form>
    );
  }

  return (
    <form onSubmit={resolve} className="flex flex-col gap-4">
      <h2>Sign in to Lemma</h2>
      {providers.map((provider) => (
        <Button
          key={provider.id}
          type="button"
          variant="secondary"
          disabled={busy}
          onClick={() => void redirectToProvider(provider.id)}
        >
          Continue with {provider.name}
        </Button>
      ))}
      {telegram}
      <label htmlFor="sign-in-email">Email address</label>
      <Input
        id="sign-in-email"
        type="email"
        autoComplete="username"
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
        required
        autoFocus
      />
      <p className="helper-copy">
        We’ll ask for a password, or send you a code — whichever this account
        uses.
      </p>
      {alert}
      <Button type="submit" disabled={busy}>
        {busy ? "Please wait…" : "Continue"}
      </Button>
      <Button type="button" variant="quiet" disabled={busy} onClick={onSignUp}>
        New to Lemma? Create an account
      </Button>
    </form>
  );
}
