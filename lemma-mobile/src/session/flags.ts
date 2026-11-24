/** Reading the two answers a feature flag comes from (`features.ts`). Pure,
 *  so a test can pin both shapes without a server. */

export interface SiteConfig {
    features?: string;
    analyticsKey?: string;
    deployment?: string;
}

/** `window.__LEMMA_SITE__={…};`, the script the web serves for itself. */
export function readSiteConfig(script: string): SiteConfig | null {
    const match = /__LEMMA_SITE__\s*=\s*(\{[\s\S]*\})\s*;?\s*$/.exec(script.trim());
    if (!match) return null;
    try {
        return JSON.parse(match[1]) as SiteConfig;
    } catch {
        return null;
    }
}

/** The flags that are on in PostHog's answer, in either of its shapes: the
 *  `/flags` endpoint's `flags: {key: {enabled}}` or `/decide`'s
 *  `featureFlags: {key: true}`. */
export function flagsOn(answer: unknown): string[] {
    const body = (answer ?? {}) as { flags?: Record<string, { enabled?: unknown }>; featureFlags?: Record<string, unknown> };
    if (body.flags && typeof body.flags === "object") {
        return Object.entries(body.flags).filter(([, flag]) => flag?.enabled === true).map(([key]) => key);
    }
    if (body.featureFlags && typeof body.featureFlags === "object") {
        return Object.entries(body.featureFlags).filter(([, value]) => value === true || (typeof value === "string" && value !== "")).map(([key]) => key);
    }
    return [];
}

/** A comma-separated list of flag names. */
export function names(list: string | undefined): string[] {
    return String(list ?? "").split(",").map((name) => name.trim()).filter(Boolean);
}
