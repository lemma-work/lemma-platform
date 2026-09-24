declare global {
    interface Window {
        __LEMMA_SITE__?: {
            analyticsKey: string;
            analyticsHost: string;
            deployment: string;
        };
    }
}
export const config = {
    SITE_URL: process.env.NEXT_PUBLIC_SITE_URL || "https://lemma.work",
    API_URL: process.env.NEXT_PUBLIC_API_URL || "",
    SUPPORT_EMAIL: process.env.NEXT_PUBLIC_SUPPORT_EMAIL || "deepak@lemma.work",
    get ANALYTICS_KEY() {
        return (
            (typeof window === "undefined"
                ? undefined
                : window.__LEMMA_SITE__?.analyticsKey) ??
            process.env.NEXT_PUBLIC_ANALYTICS_KEY ??
            ""
        );
    },
    get ANALYTICS_HOST() {
        return (
            (typeof window === "undefined"
                ? undefined
                : window.__LEMMA_SITE__?.analyticsHost) ??
            process.env.NEXT_PUBLIC_ANALYTICS_HOST ??
            "https://eu.posthog.com"
        );
    },
    get DEPLOYMENT() {
        return (
            (typeof window === "undefined"
                ? undefined
                : window.__LEMMA_SITE__?.deployment) ??
            process.env.NEXT_PUBLIC_LEMMA_DEPLOYMENT ??
            "hosted"
        );
    },
};
export function isLocalDeployment() {
    return config.DEPLOYMENT === "local";
}
