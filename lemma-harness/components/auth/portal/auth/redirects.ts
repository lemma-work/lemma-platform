import { authConfig } from "@/components/auth/portal/auth/config";
import { resolveSafeRedirectUri } from "lemma-sdk";

const REDIRECT_URI_STORAGE_KEY = "lemma.auth.redirect-uri";
const LEGACY_REDIRECT_TARGET_STORAGE_KEY = "lemma.auth.redirect-target";
const BLOCKED_REDIRECT_FALLBACK_PATH = "/__lemma-blocked-redirect";

function isLoopbackHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[/, "").replace(/\]$/, "");
  return (
    normalized === "localhost" ||
    normalized === "127.0.0.1" ||
    normalized === "::1"
  );
}

function normaliseHttpUrl(rawValue: string | null): URL | null {
  if (!rawValue) {
    return null;
  }

  try {
    const parsed = new URL(rawValue, authConfig.websiteUrl);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed : null;
  } catch {
    return null;
  }
}

export function normaliseLoopbackRedirectUri(rawRedirectUri: string | null): string | null {
  const parsed = normaliseHttpUrl(rawRedirectUri);
  if (!parsed || !isLoopbackHost(parsed.hostname)) {
    return null;
  }

  return parsed.toString();
}

export function normaliseRedirectUri(rawRedirectUri: string | null): string | null {
  if (!rawRedirectUri) {
    return null;
  }

  const fallback = new URL(BLOCKED_REDIRECT_FALLBACK_PATH, authConfig.websiteUrl).toString();
  const resolved = resolveSafeRedirectUri(rawRedirectUri, {
    siteOrigin: authConfig.websiteUrl,
    fallback: BLOCKED_REDIRECT_FALLBACK_PATH,
    // The API origin is first-party (it serves /admin and connector OAuth
    // callbacks), so allow redirecting back to it after auth — otherwise the
    // SQLAdmin login flow can't return to /admin and lands on the main app.
    allowedOrigins: authConfig.apiUrl ? [authConfig.apiUrl] : undefined,
    allowedOriginSuffixes: authConfig.appsDomainSuffix ? [authConfig.appsDomainSuffix] : undefined,
  });

  return resolved === fallback ? null : resolved;
}

export function readRawRedirectUriFromSearch(search: string): string | null {
  const params = new URLSearchParams(search);
  return params.get("redirect_uri") || params.get("redirectTo") || params.get("redirectBack");
}

export function hasRedirectUriInSearch(search: string): boolean {
  return readRawRedirectUriFromSearch(search) !== null;
}

export function readRedirectUriFromSearch(search: string): string | null {
  return normaliseRedirectUri(readRawRedirectUriFromSearch(search));
}

export function storeRedirectUri(redirectUri: string | null): void {
  if (!redirectUri) {
    return;
  }

  window.sessionStorage.setItem(REDIRECT_URI_STORAGE_KEY, redirectUri);
  window.sessionStorage.removeItem(LEGACY_REDIRECT_TARGET_STORAGE_KEY);
}

export function getStoredRedirectUri(): string | null {
  return normaliseRedirectUri(
    window.sessionStorage.getItem(REDIRECT_URI_STORAGE_KEY) ||
      window.sessionStorage.getItem(LEGACY_REDIRECT_TARGET_STORAGE_KEY),
  );
}

export function consumeStoredRedirectUri(): string | null {
  const redirectUri = getStoredRedirectUri();
  if (redirectUri) {
    window.sessionStorage.removeItem(REDIRECT_URI_STORAGE_KEY);
    window.sessionStorage.removeItem(LEGACY_REDIRECT_TARGET_STORAGE_KEY);
  }
  return redirectUri;
}

export function clearStoredRedirectUri(): void {
  window.sessionStorage.removeItem(REDIRECT_URI_STORAGE_KEY);
  window.sessionStorage.removeItem(LEGACY_REDIRECT_TARGET_STORAGE_KEY);
}

export function getDefaultPostAuthRedirect(): string {
  return (
    normaliseRedirectUri(authConfig.defaultRedirectUri || null) ||
    new URL("/", authConfig.websiteUrl).toString()
  );
}
