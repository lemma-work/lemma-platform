import { useQuery } from "@tanstack/react-query";
import { useMe } from "../data/hooks";
import { flagsOn, names, readSiteConfig } from "./flags";
import type { Server } from "./server";
import { useSession } from "./session";

/** Feature flags, decided where the web decides them
 *  (`lemma-frontend/src/site/analytics/client.ts`):
 *
 *  - on regardless when the deployment names the flag (`/site-config.js`,
 *    `features`), which is how a local stack or a deployment without
 *    analytics turns one on;
 *  - else whatever PostHog answers for the signed-in person, asked through
 *    the web's own `/ingest` proxy with the same id the web identifies them
 *    by. Only the flags are asked for: the phone sends no events.
 *
 *  Off until known and off when anything fails, as on the web, so a flagged
 *  place appears rather than vanishes. Sample data has every flag on, so
 *  every place can be looked at. */

export type FeatureFlag = "contacts";

/** Flags this build turns on, for local development: the phone's
 *  `NEXT_PUBLIC_LEMMA_FEATURES`. Set when Metro starts
 *  (`EXPO_PUBLIC_LEMMA_FEATURES=contacts npx expo start`); unset in a release. */
const BUILT = names(process.env.EXPO_PUBLIC_LEMMA_FEATURES);

export async function readFeatures(server: Server, userId: string | null): Promise<string[]> {
    const config = await fetch(`${server.web}/site-config.js`).then((response) => (response.ok ? response.text() : "")).then(readSiteConfig, () => null);
    if (!config) return BUILT;
    const forced = [...BUILT, ...names(config.features)];
    /* The web never starts PostHog on a desktop installation or without a key. */
    if (!config.analyticsKey || config.deployment === "local" || !userId) return forced;
    try {
        const response = await fetch(`${server.web}/ingest/flags/?v=2`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: config.analyticsKey, distinct_id: userId }),
        });
        if (!response.ok) return forced;
        return [...new Set([...forced, ...flagsOn(await response.json())])];
    } catch {
        return forced;
    }
}

export function useFeature(flag: FeatureFlag): boolean {
    const { mode, server } = useSession();
    const me = useMe();
    const userId = me.data?.id ?? null;
    const flags = useQuery({
        queryKey: [server.id, "features", userId],
        queryFn: () => readFeatures(server, userId),
        enabled: mode === "live" && me.isFetched,
        staleTime: 10 * 60_000,
    });
    if (mode === "sample") return true;
    return flags.data?.includes(flag) ?? false;
}
