import SuperTokens from "supertokens-web-js";
import Session from "supertokens-web-js/recipe/session/index.js";

const APP_NAME = "Lemma";
const SESSION_API_SUFFIX = "/st/auth";

let initializedSignature: string | null = null;
const unauthorisedListeners = new Set<() => void>();

function normalizePath(pathname: string): string {
  const trimmed = pathname.trim();
  if (!trimmed || trimmed === "/") {
    return "";
  }

  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash.slice(0, -1) : withLeadingSlash;
}

function resolveApiBase(apiUrl: string): { apiDomain: string; apiBasePath: string } {
  if (typeof window === "undefined") {
    throw new Error("Cookie session support requires a browser environment.");
  }

  if (/^https?:\/\//.test(apiUrl)) {
    const url = new URL(apiUrl);
    const apiPrefix = normalizePath(url.pathname);
    return {
      apiDomain: url.origin,
      apiBasePath: `${apiPrefix}${SESSION_API_SUFFIX}` || SESSION_API_SUFFIX,
    };
  }

  const apiPrefix = normalizePath(apiUrl);
  return {
    apiDomain: window.location.origin,
    apiBasePath: `${apiPrefix}${SESSION_API_SUFFIX}` || SESSION_API_SUFFIX,
  };
}

export function ensureCookieSessionSupport(
  apiUrl: string,
  onUnauthorised?: () => void,
): void {
  if (typeof window === "undefined") {
    return;
  }

  if (onUnauthorised) {
    unauthorisedListeners.add(onUnauthorised);
  }

  const { apiDomain, apiBasePath } = resolveApiBase(apiUrl);
  const signature = `${apiDomain}${apiBasePath}`;
  if (initializedSignature === signature) {
    return;
  }

  if (initializedSignature !== null && initializedSignature !== signature) {
    console.warn(
      `[lemma] SuperTokens was already initialised for ${initializedSignature}; continuing with the existing session config.`,
    );
    return;
  }

  SuperTokens.init({
    appInfo: {
      appName: APP_NAME,
      apiDomain,
      apiBasePath,
    },
    recipeList: [
      Session.init({
        tokenTransferMethod: "cookie",
        onHandleEvent: (event) => {
          if (event.action === "UNAUTHORISED") {
            unauthorisedListeners.forEach((listener) => listener());
          }
        },
      }),
    ],
  });

  initializedSignature = signature;
}
