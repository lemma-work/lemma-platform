"use client";

import { useQuery } from "@tanstack/react-query";
import { liveAvailable, podLive, useLiveStatus } from "@/live/pod-live";

/** Who in the pod has the app open now, by user id -- the socket's
 *  `presence` call, asked while a list of people is on screen. Empty when
 *  the socket is down: nobody is shown as away on a guess. */
export function useOnline(podId: string, enabled = true): ReadonlySet<string> {
    const status = useLiveStatus(podId);
    const asked = useQuery({
        queryKey: ["presence", podId] as const,
        enabled: enabled && status === "open" && liveAvailable(),
        refetchInterval: 60_000,
        staleTime: 30_000,
        retry: false,
        queryFn: async () => {
            const result = (await podLive(podId).call("presence", {})) as { online?: unknown } | null;
            return Array.isArray(result?.online) ? result.online.filter((one): one is string => typeof one === "string") : [];
        },
    });
    return new Set(status === "open" ? asked.data ?? [] : []);
}
