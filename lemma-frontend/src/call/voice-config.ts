import { useQuery } from "@tanstack/react-query";

/** Whether this install can place a voice call.
 *
 *  Asked before the button is offered, not after it is pressed: the button
 *  used to be live everywhere and answer a press with a message about a
 *  server setting the person could not see. A probe that fails says "no" —
 *  a call that cannot be checked is not one to offer. */
export async function voiceConfigured(fetcher: typeof fetch = fetch): Promise<boolean> {
    try {
        const response = await fetcher("/api/call/config", { cache: "no-store" });
        if (!response.ok) return false;
        const body: unknown = await response.json();
        return Boolean(body && typeof body === "object" && (body as { configured?: unknown }).configured === true);
    } catch {
        return false;
    }
}

/** `undefined` while the probe is out. */
export function useVoiceConfigured(): boolean | undefined {
    return useQuery({ queryKey: ["voice-configured"], queryFn: () => voiceConfigured(), staleTime: 5 * 60_000, retry: 0 }).data;
}
