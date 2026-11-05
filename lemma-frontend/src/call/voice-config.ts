import { useQuery } from "@tanstack/react-query";
import { hasApiUrl, lemma } from "@/session/client";

/** Whether this pod's server can place a voice call.
 *
 *  Asked before the button is offered, not after it is pressed: the button
 *  used to be live everywhere and answer a press with a message about a
 *  server setting the person could not see. A probe that fails says "no" —
 *  a call that cannot be checked is not one to offer. */
export async function voiceConfigured(
    podId: string,
    config: () => Promise<{ engines?: unknown }> = () => lemma(podId).calls.config(),
): Promise<boolean> {
    try {
        const engines = (await config()).engines;
        return Array.isArray(engines) && engines.length > 0;
    } catch {
        return false;
    }
}

/** `undefined` while the probe is out. Never asked with no API to ask: the
 *  client then points at a placeholder origin the page's CSP refuses, and
 *  every signed-out page logged the refusal. */
export function useVoiceConfigured(podId: string | null): boolean | undefined {
    return useQuery({
        queryKey: ["voice-configured", podId],
        queryFn: () => voiceConfigured(podId as string),
        enabled: Boolean(podId) && hasApiUrl(),
        staleTime: 5 * 60_000,
        retry: 0,
    }).data;
}
