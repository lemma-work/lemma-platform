import SuperTokens from "supertokens-auth-react";
import EmailPassword from "supertokens-auth-react/recipe/emailpassword";
import Session from "supertokens-auth-react/recipe/session";
import ThirdParty, { Google, ActiveDirectory } from "supertokens-auth-react/recipe/thirdparty";

import { authConfig, websiteBasePath } from "@/components/auth/portal/auth/config";
import {
  consumeStoredRedirectUri,
  getDefaultPostAuthRedirect,
} from "@/components/auth/portal/auth/redirects";
import {
  getStoredDesktopRequestId,
} from "@/components/auth/portal/auth/desktop";

let hasInitialised = false;

const authSurfaceStyle = `
[data-supertokens~="container"] {
  font-family: var(--font-ibm-plex-sans), "Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  width: min(100%, 26.5rem);
  margin: 0 auto;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

[data-supertokens~="row"] {
  width: 100%;
  margin: 0;
  padding: 0;
}

[data-supertokens~="superTokensBranding"] {
  display: none;
}

[data-supertokens~="headerTitle"] {
  margin: 0 0 0.55rem;
  color: var(--text-primary);
  font-family: var(--font-bricolage-grotesque), var(--font-ibm-plex-sans), ui-sans-serif, sans-serif;
  font-size: 1.8rem;
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -0.02em;
  text-align: left;
  justify-content: flex-start;
}

[data-supertokens~="headerTitle"] [data-supertokens~="backButtonPlaceholder"] {
  display: none;
}

[data-supertokens~="headerSubtitle"] {
  margin-bottom: 1.8rem;
  color: var(--text-secondary);
  text-align: left;
}

[data-supertokens~="headerSubtitle"] [data-supertokens~="secondaryText"] {
  text-align: left;
}

[data-supertokens~="secondaryText"],
[data-supertokens~="label"],
[data-supertokens~="inputLabel"],
[data-supertokens~="form_legend"] {
  color: var(--text-primary);
}

[data-supertokens~="providerContainer"] {
  padding-top: 0;
  padding-bottom: 0.85rem;
}

[data-supertokens~="button"][data-supertokens~="providerButton"] {
  min-height: 3.05rem;
  border: 1px solid var(--field-border);
  border-radius: 8px;
  background: var(--button-secondary-bg);
  color: var(--button-secondary-fg);
  box-shadow: none;
  transition: border-color 0.18s ease;
}

[data-supertokens~="button"][data-supertokens~="providerButton"]:hover {
  border-color: var(--button-secondary-border);
  background-color: var(--button-secondary-bg-hover, var(--button-secondary-bg));
  filter: none;
  transform: none;
}

[data-supertokens~="providerButtonText"] {
  color: inherit;
  font-size: 0.95rem;
  font-weight: 500;
  letter-spacing: 0;
}

[data-supertokens~="divider"] {
  margin-top: 1.35rem;
  margin-bottom: 1.35rem;
  border-bottom-color: var(--border-subtle);
}

[data-supertokens~="dividerText"] {
  color: var(--text-tertiary);
  font-size: 0.78rem;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
}

[data-supertokens~="inputWrapper"],
[data-supertokens~="inputContainer"] {
  border-radius: 8px;
  border: 1px solid var(--field-border);
  background: var(--field-bg);
  box-shadow: none;
}

[data-supertokens~="inputWrapper"]:focus-within,
[data-supertokens~="inputContainer"]:focus-within {
  border-color: var(--field-border-focus);
  background: var(--field-bg-focus, var(--field-bg));
  box-shadow: 0 0 0 4px var(--auth-ring);
}

[data-supertokens~="input"] {
  color: var(--text-primary);
  background: transparent;
  padding-top: 0.9rem;
  padding-bottom: 0.9rem;
  letter-spacing: 0;
  /* 16px minimum keeps iOS Safari from zooming the page on focus */
  font-size: 1rem;
}

[data-supertokens~="input"]::placeholder {
  color: var(--text-soft);
}

[data-supertokens~="input"]:-webkit-autofill,
[data-supertokens~="input"]:-webkit-autofill:hover,
[data-supertokens~="input"]:-webkit-autofill:focus,
[data-supertokens~="input"]:-webkit-autofill:active {
  -webkit-text-fill-color: var(--text-primary);
  box-shadow: 0 0 0 30px var(--field-bg) inset;
  caret-color: var(--text-primary);
}

[data-supertokens~="button"] {
  min-height: 2.95rem;
  border: 0;
  border-radius: 8px;
  background: var(--button-primary-bg);
  color: var(--button-primary-fg);
  box-shadow: none;
  font-size: 0.95rem;
  font-weight: 600;
  letter-spacing: 0;
  text-transform: none;
  transition: background-color 0.18s ease;
}

[data-supertokens~="button"]:hover {
  background: var(--button-primary-bg-hover, var(--button-primary-bg));
  transform: none;
}

[data-supertokens~="link"],
[data-supertokens~="textLink"] {
  color: var(--action-primary);
  font-weight: 500;
}
`;

export function ensureSuperTokensInit(): void {
  if (hasInitialised) {
    return;
  }

  SuperTokens.init({
    appInfo: {
      appName: authConfig.appName,
      apiDomain: authConfig.apiUrl,
      apiBasePath: authConfig.supertokensApiBasePath,
      apiGatewayPath: authConfig.supertokensApiGatewayPath,
      websiteDomain: authConfig.websiteUrl,
      websiteBasePath,
    },
    style: authSurfaceStyle,
    languageTranslations: {
      translations: {
        en: {
          AUTH_PAGE_HEADER_TITLE_SIGN_IN: "Sign in",
          AUTH_PAGE_HEADER_TITLE_SIGN_UP: "Create your account",
          AUTH_PAGE_HEADER_SUBTITLE_SIGN_IN_SIGN_UP_LINK: "Sign up",
          AUTH_PAGE_HEADER_SUBTITLE_SIGN_UP_SIGN_IN_LINK: "Sign in",
          EMAIL_PASSWORD_SIGN_IN_HEADER_TITLE: "Sign in",
          EMAIL_PASSWORD_SIGN_UP_HEADER_TITLE: "Create your account",
          EMAIL_PASSWORD_SIGN_IN_HEADER_SUBTITLE_SIGN_UP_LINK: "Sign up",
          EMAIL_PASSWORD_SIGN_UP_HEADER_SUBTITLE_SIGN_IN_LINK: "Sign in",
          EMAIL_PASSWORD_SIGN_IN_SUBMIT_BTN: "Sign in",
          EMAIL_PASSWORD_SIGN_UP_SUBMIT_BTN: "Create account",
          THIRD_PARTY_SIGN_IN_AND_UP_HEADER_TITLE: "Sign in",
        },
      },
    },
    recipeList: [
      Session.init({
        sessionTokenFrontendDomain: authConfig.sessionTokenDomain,
        tokenTransferMethod: "cookie",
      }),
      EmailPassword.init(),
      ThirdParty.init({
        signInAndUpFeature: {
          providers: [Google.init(), ActiveDirectory.init({name: "Microsoft"})],
        },
      }),
    ],
    getRedirectionURL: async (context) => {
      if (context.action === "SUCCESS") {
        if (window.location.pathname === `${websiteBasePath}/cli/login`) {
          return window.location.href;
        }
        const desktopRequestId = getStoredDesktopRequestId();
        if (desktopRequestId) {
          const desktopReturn = new URL(websiteBasePath, authConfig.websiteUrl);
          desktopReturn.searchParams.set("desktop_browser", "1");
          desktopReturn.searchParams.set("desktop_request", desktopRequestId);
          return desktopReturn.toString();
        }
        return consumeStoredRedirectUri() || getDefaultPostAuthRedirect();
      }

      return undefined;
    },
  });

  hasInitialised = true;
}
