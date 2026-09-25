/** What differs between deployments, decided when the server starts rather
 *  than when the app is built.
 *
 *  Next inlines every `process.env.NEXT_PUBLIC_*` it can see into the bundle
 *  at build time -- in server code as well as browser code. That is right for
 *  a hosted image built per environment, and wrong for the desktop app, which
 *  ships one build to every machine and only learns its origins when locald
 *  picks the ports; and wrong again when somebody turns on LAN or public
 *  sharing, where locald restarts this server with every URL rewritten and no
 *  build anywhere in sight.
 *
 *  So the server reads the environment it was *started* with and hands it to
 *  the browser as `window.__LEMMA_SITE__`, from `/site-config.js`, which the
 *  root layout loads before the app. Each value falls back to what the build
 *  was given, so a hosted image built with its `NEXT_PUBLIC_*` arguments and
 *  run with none (the `Dockerfile` does exactly that) is unchanged.
 *
 *  Server and browser both read through `siteRuntime()`, so a server render
 *  and the hydration that follows it cannot disagree about which deployment
 *  this is.
 */

export interface SiteRuntime {
    /** The API origin, or "" -- see `session/origins.ts` for why there is no default. */
    apiUrl: string;
    /** The SDK's auth origin; "" means `apiUrl + /auth`. */
    authUrl: string;
    /** This site's own origin, as the deployment names it. */
    siteUrl: string;
    /** Where the browser SDK writes its cookies; "" is host-only. */
    sessionTokenDomain: string;
    /** Host suffix of deployed pod apps, which sign-in may hand back to. */
    appsDomainSuffix: string;
    /** `hosted`, or `local` for a desktop installation and anyone it shares with. */
    deployment: string;
    analyticsKey: string;
    analyticsHost: string;
    /** The installer link. `null` is unset (use the public one); "" is no button. */
    desktopDownloadUrl: string | null;
    voiceProvider: string;
    /** Whether a new account must verify its email before it may do anything. */
    authEmailVerificationRequired: string;
    /** Which run of a desktop installation's runtime answered. locald's health
     *  check reads it to tell this server from one a previous run left behind. */
    runtimeInstanceId: string;
}

declare global {
    interface Window {
        __LEMMA_SITE__?: Partial<SiteRuntime>;
    }
}

/** What the build was given. Each one spelled out in full because Next finds
 *  these by matching the exact expression, and these are the ones that are
 *  *meant* to be inlined: they are the fallback. */
function built(): SiteRuntime {
    return {
        apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "",
        authUrl: process.env.NEXT_PUBLIC_AUTH_URL ?? "",
        siteUrl: process.env.NEXT_PUBLIC_SITE_URL ?? "",
        sessionTokenDomain: process.env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN ?? "",
        appsDomainSuffix: process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX ?? "",
        deployment: process.env.NEXT_PUBLIC_LEMMA_DEPLOYMENT || "hosted",
        analyticsKey: process.env.NEXT_PUBLIC_ANALYTICS_KEY ?? "",
        analyticsHost: process.env.NEXT_PUBLIC_ANALYTICS_HOST || "https://eu.posthog.com",
        desktopDownloadUrl: process.env.NEXT_PUBLIC_DESKTOP_DOWNLOAD_URL ?? null,
        voiceProvider: process.env.NEXT_PUBLIC_VOICE_PROVIDER ?? "",
        authEmailVerificationRequired: process.env.NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED ?? "",
        runtimeInstanceId: process.env.NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID ?? "",
    };
}

/** The variable each value is started with. */
const VARIABLES: Record<keyof SiteRuntime, string> = {
    apiUrl: "NEXT_PUBLIC_API_URL",
    authUrl: "NEXT_PUBLIC_AUTH_URL",
    siteUrl: "NEXT_PUBLIC_SITE_URL",
    sessionTokenDomain: "NEXT_PUBLIC_SESSION_TOKEN_DOMAIN",
    appsDomainSuffix: "NEXT_PUBLIC_APPS_DOMAIN_SUFFIX",
    deployment: "NEXT_PUBLIC_LEMMA_DEPLOYMENT",
    analyticsKey: "NEXT_PUBLIC_ANALYTICS_KEY",
    analyticsHost: "NEXT_PUBLIC_ANALYTICS_HOST",
    desktopDownloadUrl: "NEXT_PUBLIC_DESKTOP_DOWNLOAD_URL",
    voiceProvider: "NEXT_PUBLIC_VOICE_PROVIDER",
    authEmailVerificationRequired: "NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED",
    runtimeInstanceId: "NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID",
};

/** The same values, read from the environment this server was started with.
 *
 *  Through a variable and a computed key, which is the whole trick: that is
 *  the one spelling Next's inliner does not match, so this is the live
 *  environment rather than a copy of the build's. Present-but-blank wins over
 *  the build: locald sets `NEXT_PUBLIC_SESSION_TOKEN_DOMAIN=""` on purpose. */
export function startedWith(environment: Record<string, string | undefined>): SiteRuntime {
    const fallback = built();
    const resolved = { ...fallback };
    for (const key of Object.keys(VARIABLES) as (keyof SiteRuntime)[]) {
        const value = environment[VARIABLES[key]];
        if (value !== undefined) (resolved as Record<string, string | null>)[key] = value;
    }
    /* Two with defaults, where blank means "use it" rather than "none". */
    resolved.deployment ||= fallback.deployment;
    resolved.analyticsHost ||= fallback.analyticsHost;
    return resolved;
}

/** This deployment, asked from wherever the caller runs. */
export function siteRuntime(): SiteRuntime {
    if (typeof window === "undefined") {
        const environment: Record<string, string | undefined> = process.env;
        return startedWith(environment);
    }
    /* A page whose `/site-config.js` failed, or a test with no server behind
       it, still has what it was built with. */
    return { ...built(), ...window.__LEMMA_SITE__ };
}

/** The script `/site-config.js` answers with. `<` is escaped so no value can
 *  close the tag it is embedded in. */
export function siteRuntimeScript(runtime: SiteRuntime): string {
    return "window.__LEMMA_SITE__=" + JSON.stringify(runtime).replace(/</g, "\\u003c") + ";";
}
