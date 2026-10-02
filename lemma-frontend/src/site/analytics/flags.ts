"use client";

import { useSyncExternalStore } from "react";

import { isFeatureOn, subscribeToFlags, type FeatureFlag } from "@/site/analytics/client";

/** Whether a feature is on for whoever is signed in. Off on the server and
 *  until PostHog has answered, so a flagged place appears rather than vanishes. */
export function useFeature(flag: FeatureFlag): boolean {
    return useSyncExternalStore(subscribeToFlags, () => isFeatureOn(flag), () => false);
}
