"use client";

/* eslint-disable react-hooks/set-state-in-effect, @next/next/no-img-element */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { LockKeyhole, RefreshCw, ShieldCheck } from "@/components/ui/icons";

import {
  clearStoredRedirectUri,
  hasRedirectUriInSearch,
  readRedirectUriFromSearch,
  storeRedirectUri,
} from "@/components/auth/portal/auth/redirects";
import {
  resolveDestinationHeadline,
  type RedirectDestination,
} from "@/components/auth/portal/auth-portal-metadata";

export type HeroCopy = {
  eyebrow: string;
  title: string;
  description: string;
};

export type AuthScreenLayoutProps = {
  destination?: RedirectDestination | null;
  heroCopy?: HeroCopy;
  children: ReactNode;
};

export type StatusPanelProps = {
  eyebrow: string;
  title: string;
  description: string;
  tone?: "neutral" | "danger";
  children?: ReactNode;
};

export const defaultHeroCopy: HeroCopy = {
  eyebrow: "Lemma account",
  title: "Sign in once, continue anywhere.",
  description:
    "Your Lemma session is shared with every app that signs in through it.",
};

export const destinationHeroCopy: HeroCopy = {
  eyebrow: "Continue to",
  title: "",
  description: "Use your Lemma account to continue.",
};

export function LemmaLogo() {
  return (
    <span className="lemma-logo" aria-label="Lemma">
      <LemmaMark />
      <span className="lemma-wordmark">lemma</span>
    </span>
  );
}

export function LemmaMark() {
  return (
    <span className="lemma-mark" aria-hidden="true">
      <span className="lemma-bar lemma-bar-short" />
      <span className="lemma-bar lemma-bar-medium" />
      <span className="lemma-bar lemma-bar-tall" />
    </span>
  );
}

export function AuthIcon({ name }: { name: "lock" | "refresh" | "shield" }) {
  const Icon = name === "refresh" ? RefreshCw : name === "lock" ? LockKeyhole : ShieldCheck;
  return <Icon className="auth-icon" aria-hidden="true" weight="regular" />;
}

export function DestinationFavicon({
  destination,
}: {
  destination: RedirectDestination;
}) {
  const [loadedIconUrl, setLoadedIconUrl] = useState<string | null>(null);
  const fallbackLetter =
    (destination.title || destination.name || destination.host)
      .slice(0, 1)
      .toUpperCase() || "A";
  const iconCandidates = useMemo(
    () =>
      [
        destination.faviconUrl,
        new URL("/favicon.ico", destination.origin).toString(),
        new URL("/favicon.svg", destination.origin).toString(),
        new URL("/apple-touch-icon.png", destination.origin).toString(),
        `https://www.google.com/s2/favicons?domain=${encodeURIComponent(destination.host)}&sz=128`,
      ].filter(
        (value, index, values): value is string =>
          Boolean(value) && values.indexOf(value) === index,
      ),
    [destination.faviconUrl, destination.host, destination.origin],
  );

  useEffect(() => {
    let isActive = true;
    setLoadedIconUrl(null);

    const loadIcon = (candidateIndex: number) => {
      const candidateUrl = iconCandidates[candidateIndex];
      if (!candidateUrl) {
        return;
      }

      const image = new Image();
      image.onload = () => {
        if (isActive) {
          setLoadedIconUrl(candidateUrl);
        }
      };
      image.onerror = () => {
        if (isActive) {
          loadIcon(candidateIndex + 1);
        }
      };
      image.src = candidateUrl;
    };

    loadIcon(0);

    return () => {
      isActive = false;
    };
  }, [iconCandidates]);

  if (!loadedIconUrl) {
    return (
      <span className="destination-favicon-fallback">{fallbackLetter}</span>
    );
  }

  return (
    <img className="destination-favicon-image" src={loadedIconUrl} alt="" />
  );
}

export function DestinationPanel({
  destination,
  heroCopy,
}: {
  destination?: RedirectDestination | null;
  heroCopy: HeroCopy;
}) {
  return (
    <div className="destination-panel">
      <div className="destination-panel-grid">
        <div className="visual-brand">
          <LemmaLogo />
        </div>

        <div className="destination-context">
          <p className="destination-eyebrow">{heroCopy.eyebrow}</p>
          <h1>
            {destination
              ? resolveDestinationHeadline(destination)
              : heroCopy.title}
          </h1>
          <p className="hero-description">
            {destination
              ? destination.description || heroCopy.description
              : heroCopy.description}
          </p>
        </div>

        {destination ? (
          <div className="redirect-chip-area">
            <div
              className="redirect-chip"
              aria-label={`You'll be sent back to ${destination.host} after signing in`}
            >
              <span className="redirect-chip-mark" aria-hidden="true">
                <LemmaMark />
              </span>
              <span className="redirect-chip-dots" aria-hidden="true">
                ····
              </span>
              <span className="redirect-chip-lock" aria-hidden="true">
                <AuthIcon name="lock" />
              </span>
              <span className="redirect-chip-dots" aria-hidden="true">
                ····
              </span>
              <DestinationFavicon destination={destination} />
              <span className="redirect-chip-host">{destination.host}</span>
            </div>
            <p className="redirect-chip-caption">
              {"You'll return here after signing in"}
            </p>
          </div>
        ) : (
          <div className="redirect-chip-area" />
        )}
      </div>
    </div>
  );
}

export function MobileDestinationHeader({
  destination,
}: {
  destination?: RedirectDestination | null;
}) {
  return (
    <header className="auth-mobile-header">
      <span>Continue to</span>
      <strong>
        {destination ? resolveDestinationHeadline(destination) : "Lemma"}
      </strong>
      <p>
        {destination
          ? "Use your Lemma account to continue."
          : "Sign in to continue your Lemma session."}
      </p>
    </header>
  );
}

export function AuthScreenLayout({
  destination,
  heroCopy,
  children,
}: AuthScreenLayoutProps) {
  const resolvedHeroCopy =
    heroCopy || (destination ? destinationHeroCopy : defaultHeroCopy);

  return (
    <section className="auth-screen">
      <section className="visual-panel">
        <DestinationPanel
          destination={destination}
          heroCopy={resolvedHeroCopy}
        />
      </section>

      <aside className="auth-panel-shell">
        <MobileDestinationHeader destination={destination} />

        {children}
      </aside>
    </section>
  );
}

export function StatusPanel({
  eyebrow,
  title,
  description,
  tone = "neutral",
  children,
}: StatusPanelProps) {
  return (
    <section
      className={`status-panel${tone === "danger" ? " status-panel-danger" : ""}`}
    >
      <span className="eyebrow">{eyebrow}</span>
      <h2>{title}</h2>
      <p>{description}</p>
      {children}
    </section>
  );
}

export function PendingPanel({
  message,
  detail,
}: {
  message: string;
  detail?: string;
}) {
  return (
    <div className="pending-state" role="status">
      <div className="spinner" aria-hidden="true" />
      <p className="pending-message">{message}</p>
      {detail ? <p className="pending-detail">{detail}</p> : null}
    </div>
  );
}

export function RedirectStateSync(): null {
  const location = useLocation();

  useEffect(() => {
    const search = location.search || window.location.search;
    const redirectUri = readRedirectUriFromSearch(search);
    if (redirectUri) {
      storeRedirectUri(redirectUri);
      return;
    }

    if (hasRedirectUriInSearch(search)) {
      clearStoredRedirectUri();
    }
  }, [location.search]);

  return null;
}

export function LoadingState({
  message,
  destination,
}: {
  message: string;
  destination?: RedirectDestination | null;
}) {
  return (
    <AuthScreenLayout destination={destination}>
      <PendingPanel message={message} />
    </AuthScreenLayout>
  );
}
