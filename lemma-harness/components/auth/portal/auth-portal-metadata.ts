"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";

import { authConfig } from "@/components/auth/portal/auth/config";

export type UrlSnapshot = {
  pathname: string;
  search: string;
  hash: string;
};

export type DestinationMetadataStatus = "idle" | "loading" | "ready" | "unavailable";

export type RedirectDestination = {
  redirectUri: string;
  origin: string;
  host: string;
  displayUrl: string;
  title: string;
  name: string;
  description: string | null;
  faviconUrl: string | null;
  hasReadableMetadata: boolean;
  metadataStatus: DestinationMetadataStatus;
};

export type DestinationMetadata = Pick<
  RedirectDestination,
  "title" | "name" | "description" | "faviconUrl"
>;

export type LemmaAppMetadata = {
  title?: unknown;
  name?: unknown;
  siteName?: unknown;
  appName?: unknown;
  description?: unknown;
  icon?: unknown;
  iconUrl?: unknown;
  faviconUrl?: unknown;
};

export function readWindowUrlSnapshot(): UrlSnapshot {
  return {
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
  };
}

export function getDestinationLabel(redirectUri: string | null): string | null {
  if (!redirectUri) {
    return null;
  }

  try {
    return new URL(redirectUri).host.replace(/^www\./, "");
  } catch {
    return null;
  }
}

export function compactWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function trimDisplayValue(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }

  return `${value.slice(0, Math.max(0, maxLength - 1))}…`;
}

export function titleCaseToken(token: string): string {
  const upperToken = token.toUpperCase();
  const acronyms = new Set([
    "AI",
    "API",
    "BI",
    "CLI",
    "CRM",
    "HR",
    "IAM",
    "ID",
    "OS",
    "SSO",
    "UI",
  ]);

  if (acronyms.has(upperToken)) {
    return upperToken;
  }

  return `${token.slice(0, 1).toUpperCase()}${token.slice(1).toLowerCase()}`;
}

export function humanizeUriTitle(url: URL): string {
  const host = url.hostname.replace(/^www\./, "");
  const labels = host.split(".").filter(Boolean);
  // App apps are served at "<appName>.<appsDomainSuffix>"; when the host is on
  // that suffix, title it from the app subdomain rather than the bare hostname.
  const appsSuffix = authConfig.appsDomainSuffix?.replace(/^\./, "").toLowerCase();
  const appsAnchor = appsSuffix ? appsSuffix.split(".")[0] : undefined;
  const appsAnchorIndex =
    appsSuffix && host.endsWith(appsSuffix) && appsAnchor
      ? labels.indexOf(appsAnchor)
      : -1;
  const sourceLabel =
    appsAnchorIndex > 0 ? labels[appsAnchorIndex - 1] : labels[0] || host;
  const words = sourceLabel
    .split(/[^a-zA-Z0-9]+/)
    .map((word) => word.trim())
    .filter(Boolean)
    .map(titleCaseToken);

  return words.length > 0 ? words.join(" ") : host;
}

export function getDisplayUrl(url: URL): string {
  return url.toString();
}

export function resolveDestinationHeadline(destination: RedirectDestination): string {
  const candidates = [destination.name, destination.title, destination.host];

  for (const candidate of candidates) {
    const cleaned = compactWhitespace((candidate || "").split("|")[0]);
    if (cleaned) {
      return trimDisplayValue(cleaned, 48);
    }
  }

  return destination.host;
}

export function readStringValue(value: unknown): string {
  return typeof value === "string" ? compactWhitespace(value) : "";
}

export function resolveMetadataUrl(value: unknown, baseUrl: string): string | null {
  const rawUrl = readStringValue(value);
  if (!rawUrl) {
    return null;
  }

  try {
    return new URL(rawUrl, baseUrl).toString();
  } catch {
    return null;
  }
}

export function getRedirectDestinationFallback(
  redirectUri: string | null,
): RedirectDestination | null {
  if (!redirectUri) {
    return null;
  }

  try {
    const parsed = new URL(redirectUri);
    const host = parsed.host.replace(/^www\./, "");
    const fallbackTitle = humanizeUriTitle(parsed);

    return {
      redirectUri: parsed.toString(),
      origin: parsed.origin,
      host,
      displayUrl: getDisplayUrl(parsed),
      title: trimDisplayValue(fallbackTitle, 96),
      name: fallbackTitle,
      description: null,
      faviconUrl: null,
      hasReadableMetadata: false,
      metadataStatus: "idle",
    };
  } catch {
    return null;
  }
}

export function getRejectedRedirectDestination(
  rawRedirectUri: string | null,
): RedirectDestination | null {
  if (!rawRedirectUri) {
    return null;
  }

  try {
    const parsed = new URL(rawRedirectUri, authConfig.websiteUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return null;
    }

    const host = parsed.host.replace(/^www\./, "");
    const fallbackTitle = humanizeUriTitle(parsed);

    return {
      redirectUri: parsed.toString(),
      origin: parsed.origin,
      host,
      displayUrl: getDisplayUrl(parsed),
      title: trimDisplayValue(fallbackTitle, 96),
      name: fallbackTitle,
      description: null,
      faviconUrl: null,
      hasReadableMetadata: false,
      metadataStatus: "unavailable",
    };
  } catch {
    return null;
  }
}

export function readMetaContent(
  document: Document,
  selectors: string[],
): string | null {
  for (const selector of selectors) {
    const value = document.querySelector<HTMLMetaElement>(selector)?.content;
    const compactValue = value ? compactWhitespace(value) : "";

    if (compactValue) {
      return compactValue;
    }
  }

  return null;
}

export function readFaviconUrl(document: Document, baseUrl: string): string | null {
  const iconLink = document.querySelector<HTMLLinkElement>(
    [
      'link[rel~="icon"][href]',
      'link[rel="shortcut icon"][href]',
      'link[rel="apple-touch-icon"][href]',
      'link[rel="apple-touch-icon-precomposed"][href]',
      'link[rel="mask-icon"][href]',
    ].join(","),
  );

  const href = iconLink?.getAttribute("href");
  if (!href) {
    return null;
  }

  try {
    return new URL(href, baseUrl).toString();
  } catch {
    return null;
  }
}

export function readLemmaAppMetadata(
  rawMetadata: LemmaAppMetadata,
  baseUrl: string,
): DestinationMetadata {
  const title = readStringValue(
    rawMetadata.title || rawMetadata.name || rawMetadata.appName,
  );
  const name = readStringValue(
    rawMetadata.siteName ||
      rawMetadata.name ||
      rawMetadata.appName ||
      rawMetadata.title,
  );
  const description = readStringValue(rawMetadata.description);
  const faviconUrl =
    resolveMetadataUrl(rawMetadata.iconUrl, baseUrl) ||
    resolveMetadataUrl(rawMetadata.icon, baseUrl) ||
    resolveMetadataUrl(rawMetadata.faviconUrl, baseUrl);

  return {
    title,
    name,
    description: description || null,
    faviconUrl,
  };
}

export function readHtmlDestinationMetadata(
  html: string,
  baseUrl: string,
): DestinationMetadata {
  const document = new DOMParser().parseFromString(html, "text/html");
  const title = readMetaContent(document, [
    'meta[property="og:title"]',
    'meta[name="twitter:title"]',
  ]);
  const siteName = readMetaContent(document, [
    'meta[property="og:site_name"]',
    'meta[name="connector-name"]',
    'meta[name="apple-mobile-web-app-title"]',
  ]);
  const description = readMetaContent(document, [
    'meta[name="description"]',
    'meta[property="og:description"]',
    'meta[name="twitter:description"]',
  ]);
  const documentTitle = compactWhitespace(
    document.querySelector("title")?.textContent || "",
  );
  const resolvedTitle = title || documentTitle || siteName || "";

  return {
    title: resolvedTitle,
    name: siteName || resolvedTitle,
    description,
    faviconUrl: readFaviconUrl(document, baseUrl),
  };
}

export async function fetchWellKnownDestinationMetadata(
  fallback: RedirectDestination,
  signal: AbortSignal,
): Promise<DestinationMetadata> {
  const metadataUrl = new URL(
    "/.well-known/lemma-app.json",
    fallback.origin,
  ).toString();
  const response = await fetch(metadataUrl, {
    credentials: "omit",
    signal,
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(
      `Well-known metadata request failed with ${response.status}`,
    );
  }

  return readLemmaAppMetadata(
    (await response.json()) as LemmaAppMetadata,
    fallback.origin,
  );
}

export async function fetchHtmlDestinationMetadata(
  fallback: RedirectDestination,
  signal: AbortSignal,
): Promise<DestinationMetadata> {
  const response = await fetch(fallback.redirectUri, {
    credentials: "omit",
    signal,
    headers: {
      Accept: "text/html,application/xhtml+xml",
    },
  });

  if (!response.ok) {
    throw new Error(`HTML metadata request failed with ${response.status}`);
  }

  return readHtmlDestinationMetadata(
    await response.text(),
    response.url || fallback.redirectUri,
  );
}

export async function fetchDestinationMetadata(
  fallback: RedirectDestination,
  signal: AbortSignal,
): Promise<DestinationMetadata> {
  try {
    return await fetchWellKnownDestinationMetadata(fallback, signal);
  } catch {
    return fetchHtmlDestinationMetadata(fallback, signal);
  }
}

export function isUsefulMetadataValue(
  value: string | null,
  fallback: RedirectDestination,
): boolean {
  const normalizedValue = compactWhitespace(value || "").toLowerCase();
  if (!normalizedValue) {
    return false;
  }

  const authAppName = authConfig.appName.toLowerCase();
  return (
    normalizedValue !== fallback.title.toLowerCase() &&
    normalizedValue !== fallback.name.toLowerCase() &&
    normalizedValue !== fallback.host.toLowerCase() &&
    normalizedValue !== fallback.displayUrl.toLowerCase() &&
    normalizedValue !== authAppName
  );
}

export function useRedirectDestinationMetadata(
  redirectUri: string | null,
): RedirectDestination | null {
  const [destination, setDestination] = useState<RedirectDestination | null>(
    () => getRedirectDestinationFallback(redirectUri),
  );

  useEffect(() => {
    const fallback = getRedirectDestinationFallback(redirectUri);
    if (!fallback) {
      setDestination(null);
      return;
    }

    setDestination(fallback);

    let isActive = true;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      controller.abort();
    }, 4500);

    void fetchDestinationMetadata(fallback, controller.signal)
      .then((metadata) => {
        const metadataTitle = compactWhitespace(metadata.title || "");
        const metadataName = compactWhitespace(metadata.name || "");
        const metadataDescription = compactWhitespace(
          metadata.description || "",
        );
        const hasUsefulTitle = isUsefulMetadataValue(metadataTitle, fallback);
        const hasUsefulName = isUsefulMetadataValue(metadataName, fallback);
        const hasUsefulDescription = isUsefulMetadataValue(
          metadataDescription,
          fallback,
        );
        const hasUsefulFavicon = Boolean(
          metadata.faviconUrl &&
          metadataTitle.toLowerCase() !== authConfig.appName.toLowerCase(),
        );
        const hasReadableMetadata =
          hasUsefulTitle ||
          hasUsefulName ||
          hasUsefulDescription ||
          hasUsefulFavicon;

        if (!isActive || !hasReadableMetadata) {
          return;
        }

        setDestination({
          ...fallback,
          title: trimDisplayValue(
            hasUsefulTitle ? metadataTitle : fallback.title,
            96,
          ),
          name: trimDisplayValue(
            hasUsefulName ? metadataName : fallback.name,
            56,
          ),
          description: hasUsefulDescription
            ? trimDisplayValue(metadataDescription, 180)
            : null,
          faviconUrl: hasUsefulFavicon
            ? metadata.faviconUrl
            : fallback.faviconUrl,
          hasReadableMetadata,
          metadataStatus: hasReadableMetadata ? "ready" : "unavailable",
        });
      })
      .catch(() => {
        return;
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
      });

    return () => {
      isActive = false;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [redirectUri]);

  return destination;
}
