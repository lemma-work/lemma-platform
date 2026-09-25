import { siteRuntime } from "./runtime";

export const config = {
    get SITE_URL() {
        return siteRuntime().siteUrl || "https://lemma.work";
    },
    get API_URL() {
        return siteRuntime().apiUrl;
    },
    /* The company's address, not the deployment's: it appears in legal pages
       prerendered at build time, where only the build's value exists. */
    SUPPORT_EMAIL: process.env.NEXT_PUBLIC_SUPPORT_EMAIL || "deepak@lemma.work",
    get ANALYTICS_KEY() {
        return siteRuntime().analyticsKey;
    },
    get ANALYTICS_HOST() {
        return siteRuntime().analyticsHost;
    },
    get DEPLOYMENT() {
        return siteRuntime().deployment;
    },
};

/** A desktop installation, or somebody it is shared with.
 *
 *  Read at run time, never from the build: the same build is served by hosted
 *  Lemma and by every desktop app, and only the environment the server was
 *  started with says which one this is. */
export function isLocalDeployment() {
    return config.DEPLOYMENT === "local";
}
