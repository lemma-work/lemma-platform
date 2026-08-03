// Runtime configuration for Next.js
// This allows env vars to be set at runtime rather than build time

declare global {
  interface Window {
    __ENV?: {
      NEXT_PUBLIC_API_URL?: string;
      NEXT_PUBLIC_AUTH_URL?: string;
      NEXT_PUBLIC_SITE_URL?: string;
      NEXT_PUBLIC_SESSION_TOKEN_DOMAIN?: string;
      NEXT_PUBLIC_APPS_DOMAIN_SUFFIX?: string;
      NEXT_PUBLIC_SUPPORT_EMAIL?: string;
      NEXT_PUBLIC_LEMMA_DEPLOYMENT?: string;
    };
  }
}

export interface RuntimeConfig {
  API_URL: string;
  AUTH_URL: string;
  SITE_URL: string;
  SESSION_TOKEN_DOMAIN: string;
  APPS_DOMAIN_SUFFIX: string;
  SUPPORT_EMAIL: string;
  /** `"local"` for a Lemma Desktop installation, `"hosted"` otherwise. */
  DEPLOYMENT: string;
}

// Public contact address shown on legal pages and support links. Override per
// deployment with NEXT_PUBLIC_SUPPORT_EMAIL.
const DEFAULT_SUPPORT_EMAIL = "deepak@lemma.work";

function getRuntimeConfig(): RuntimeConfig {
  // Server-side: use process.env directly (runtime env vars work in standalone mode)
  if (typeof window === "undefined") {
    const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
    return {
      API_URL: process.env.NEXT_PUBLIC_API_URL || "https://api.localhost",
      AUTH_URL: process.env.NEXT_PUBLIC_AUTH_URL || siteUrl,
      SITE_URL: siteUrl,
      SESSION_TOKEN_DOMAIN: process.env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN || "",
      APPS_DOMAIN_SUFFIX: process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX || "",
      SUPPORT_EMAIL: process.env.NEXT_PUBLIC_SUPPORT_EMAIL || DEFAULT_SUPPORT_EMAIL,
      DEPLOYMENT: process.env.NEXT_PUBLIC_LEMMA_DEPLOYMENT || "hosted",
    };
  }

  // Client-side: prefer window.__ENV (injected at runtime), fall back to process.env
  const runtimeEnv = window.__ENV || {};
  const siteUrl = runtimeEnv.NEXT_PUBLIC_SITE_URL || process.env.NEXT_PUBLIC_SITE_URL || window.location.origin;
  const inferredLocalApiUrl =
    window.location.hostname === "localhost" && window.location.port === "3711"
      ? "http://localhost:8711"
      : "https://api.localhost";
  return {
    API_URL: runtimeEnv.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_URL || inferredLocalApiUrl,
    AUTH_URL: runtimeEnv.NEXT_PUBLIC_AUTH_URL || process.env.NEXT_PUBLIC_AUTH_URL || siteUrl,
    SITE_URL: siteUrl,
    SESSION_TOKEN_DOMAIN: runtimeEnv.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN || process.env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN || "",
    APPS_DOMAIN_SUFFIX: runtimeEnv.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX || process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX || "",
    SUPPORT_EMAIL: runtimeEnv.NEXT_PUBLIC_SUPPORT_EMAIL || process.env.NEXT_PUBLIC_SUPPORT_EMAIL || DEFAULT_SUPPORT_EMAIL,
    DEPLOYMENT:
      runtimeEnv.NEXT_PUBLIC_LEMMA_DEPLOYMENT || process.env.NEXT_PUBLIC_LEMMA_DEPLOYMENT || "hosted",
  };
}

export const config = getRuntimeConfig();

/**
 * Is this frontend served by a Lemma Desktop installation?
 *
 * True for every visitor to a local install — the desktop webview, a phone on
 * the same Wi-Fi, someone holding a public link — because locald sets
 * `NEXT_PUBLIC_LEMMA_DEPLOYMENT` on the frontend process it supervises. The
 * `__LEMMA_DESKTOP__` global only covers the first of those, so anything that
 * must hold for all three has to read this instead.
 */
export function isLocalDeployment(): boolean {
  return config.DEPLOYMENT === "local";
}

// Helper to get site URL with trailing slash for auth redirects
export function getSiteUrlWithSlash(): string {
  const url = config.SITE_URL;
  return url.endsWith('/') ? url : `${url}/`;
}
