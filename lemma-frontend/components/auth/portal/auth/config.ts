const DEFAULT_APP_NAME = "Lemma Auth";
const DEFAULT_API_URL = "http://localhost:8000";
const DEFAULT_DOCKER_API_URL = "http://localhost:8711";
const DEFAULT_SUPERTOKENS_API_BASE_PATH = "/auth";
const DEFAULT_SUPERTOKENS_API_GATEWAY_PATH = "/st";
const DEFAULT_WEBSITE_BASE_PATH = "/auth";

declare global {
  interface Window {
    __LEMMA_AUTH_CONFIG__?: Record<string, string | undefined>;
  }
}

function getRuntimeValue(key: string): string | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }

  const nextRuntimeEnv = window.__ENV as Record<string, string | undefined> | undefined;
  const runtimeValue = nextRuntimeEnv?.[`NEXT_PUBLIC_${key}`] || window.__LEMMA_AUTH_CONFIG__?.[key];
  return runtimeValue && runtimeValue.trim() ? runtimeValue.trim() : undefined;
}

function getConfigValue(runtimeKey: string, nextValue: string | undefined): string | undefined {
  return getRuntimeValue(runtimeKey) || nextValue;
}

function getBooleanConfigValue(
  runtimeKey: string,
  nextValue: string | undefined,
  fallback: boolean,
): boolean {
  const value = getConfigValue(runtimeKey, nextValue)?.toLowerCase();
  if (!value) return fallback;
  return !["0", "false", "no", "off"].includes(value);
}

function getDefaultApiUrl(): string {
  if (
    typeof window !== "undefined" &&
    window.location.hostname === "localhost" &&
    window.location.port === "3711"
  ) {
    return DEFAULT_DOCKER_API_URL;
  }

  return DEFAULT_API_URL;
}

function normalisePath(rawPath: string | undefined, fallback: string): string {
  const candidate = rawPath?.trim() || fallback;
  const withLeadingSlash = candidate.startsWith("/") ? candidate : `/${candidate}`;

  if (withLeadingSlash.length > 1 && withLeadingSlash.endsWith("/")) {
    return withLeadingSlash.slice(0, -1);
  }

  return withLeadingSlash;
}

function normaliseBaseUrl(rawUrl: string | undefined, fallback: string): string {
  const candidate = rawUrl?.trim() || fallback;

  try {
    const parsed = new URL(candidate, getWebsiteUrl());
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return fallback;
  }
}

function getWebsiteUrl(): string {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }

  return process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
}

// The shared session-cookie domain that lets a single login be reused across
// sibling subdomains (e.g. app apps). Self-hosters set this to their own apex
// domain via NEXT_PUBLIC_SHARED_SESSION_DOMAIN; it stays unset (single-host
// cookies only) when not configured.
function getSharedSessionDomain(): string | undefined {
  const configured = getConfigValue(
    "SHARED_SESSION_DOMAIN",
    process.env.NEXT_PUBLIC_SHARED_SESSION_DOMAIN,
  )?.trim();
  return configured || undefined;
}

function getDefaultSessionTokenDomain(): string | undefined {
  const shared = getSharedSessionDomain();
  if (!shared) {
    return undefined;
  }

  const normalized = shared.replace(/^\./, "").toLowerCase();
  const hostname = getWebsiteHostname();
  if (hostname === normalized || hostname.endsWith(`.${normalized}`)) {
    return shared.startsWith(".") ? shared : `.${shared}`;
  }
  return undefined;
}

function normaliseSessionTokenDomain(rawDomain: string | undefined): string | undefined {
  const value = rawDomain?.trim();
  if (!value) {
    return undefined;
  }

  const websiteHostname = getWebsiteHostname();
  const candidate = value.replace(/^\./, "").toLowerCase();

  if (
    websiteHostname === candidate ||
    websiteHostname.endsWith(`.${candidate}`)
  ) {
    return value;
  }

  return undefined;
}

function getWebsiteHostname(): string {
  try {
    return new URL(getWebsiteUrl()).hostname.toLowerCase();
  } catch {
    return "localhost";
  }
}

const apiRequestBaseUrl = normaliseBaseUrl(
  getConfigValue("API_URL", process.env.NEXT_PUBLIC_API_URL),
  getDefaultApiUrl(),
);

export const authConfig = {
  appName: getConfigValue("APP_NAME", process.env.NEXT_PUBLIC_APP_NAME) || DEFAULT_APP_NAME,
  apiRequestBaseUrl,
  apiUrl: new URL(apiRequestBaseUrl, getWebsiteUrl()).origin,
  websiteUrl: getWebsiteUrl(),
  emailVerificationRequired: getBooleanConfigValue(
    "AUTH_EMAIL_VERIFICATION_REQUIRED",
    process.env.NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED,
    true,
  ),
  websiteBasePath: normalisePath(
    getConfigValue("WEBSITE_BASE_PATH", process.env.NEXT_PUBLIC_AUTH_WEBSITE_BASE_PATH),
    DEFAULT_WEBSITE_BASE_PATH,
  ),
  supertokensApiBasePath: normalisePath(
    getConfigValue(
      "SUPERTOKENS_API_BASE_PATH",
      process.env.NEXT_PUBLIC_SUPERTOKENS_API_BASE_PATH,
    ),
    DEFAULT_SUPERTOKENS_API_BASE_PATH,
  ),
  supertokensApiGatewayPath: normalisePath(
    getConfigValue(
      "SUPERTOKENS_API_GATEWAY_PATH",
      process.env.NEXT_PUBLIC_SUPERTOKENS_API_GATEWAY_PATH,
    ),
    DEFAULT_SUPERTOKENS_API_GATEWAY_PATH,
  ),
  defaultRedirectUri:
    getConfigValue(
      "DEFAULT_REDIRECT_URI",
      process.env.NEXT_PUBLIC_AUTH_DEFAULT_REDIRECT_URI,
    ) || undefined,
  sessionTokenDomain: normaliseSessionTokenDomain(
    getConfigValue(
      "SESSION_TOKEN_DOMAIN",
      process.env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN,
    ),
  ) || getDefaultSessionTokenDomain(),
  // Domain suffix under which pod app apps are served (e.g. "apps.example.com").
  // When set, redirect destinations on that suffix are titled from their
  // subdomain label. Optional; leave unset if you don't host app apps.
  appsDomainSuffix:
    getConfigValue("APPS_DOMAIN_SUFFIX", process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX)?.trim() ||
    undefined,
};

export function buildApiUrl(path: string): string {
  const cleanPath = path.startsWith("/") ? path.slice(1) : path;
  return new URL(cleanPath, `${authConfig.apiRequestBaseUrl}/`).toString();
}

export const websiteBasePath = authConfig.websiteBasePath;
export const refreshSessionPath = "/refresh-session";
