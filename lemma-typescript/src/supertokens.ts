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
        /**
         * How many times one request may be refreshed-and-retried before the
         * session is called unusable. The library default is 10.
         *
         * This is the init the workspace and every pod app actually run --
         * `LemmaAuth` constructs it -- while the ceiling that was set to 2 sits
         * on the auth portal's own `SuperTokens.init`, which only the `/auth`
         * routes reach. So the pages that make the most requests were the ones
         * still retrying ten times each.
         *
         * That is the amplifier, not the cause: a refresh can answer 500
         * forever when a browser holds two session cookies from a
         * cookie-domain change, and at ten attempts per request across the
         * queries a workspace screen makes, one install logged 30 refusals and
         * 17 500s before anyone looked. Two is enough to ride out an access
         * token that expired between being read and being sent.
         */
        maxRetryAttemptsForSessionRefresh: 2,
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
