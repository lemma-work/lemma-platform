import { stUrl } from "./config";

/** Which "Continue with …" buttons this deployment can honour.
 *
 *  Asked of SuperTokens' own `GET /loginmethods` rather than of a flag of ours:
 *  it answers from the provider list the backend actually registered
 *  (`build_thirdparty_providers`, one entry per provider whose client id and
 *  secret are set), so the buttons cannot disagree with what a click would
 *  reach. A button for a provider nobody configured ends at the provider's
 *  "unknown client" page, which is the worst place to learn it.
 */
export type ProviderId = "google" | "active-directory";

const KNOWN: readonly ProviderId[] = ["google", "active-directory"];

function record(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
}

/** The providers named in a `/loginmethods` answer, in this app's order.
 *
 *  Anything malformed reads as none: a button hidden by mistake still leaves
 *  email sign-in, while one shown by mistake leads nowhere. */
export function configuredProviders(body: unknown): ProviderId[] {
    if (!record(body) || body.status !== "OK" || !record(body.thirdParty)) return [];
    if (body.thirdParty.enabled === false || !Array.isArray(body.thirdParty.providers)) return [];
    const named = new Set(
        body.thirdParty.providers
            .map((provider: unknown) => (record(provider) ? provider.id : null))
            .filter((id: unknown): id is string => typeof id === "string"),
    );
    return KNOWN.filter((id) => named.has(id));
}

export async function fetchConfiguredProviders(fetcher: typeof fetch = fetch): Promise<ProviderId[]> {
    const url = stUrl("/loginmethods");
    if (!url) return [];
    try {
        const response = await fetcher(url);
        if (!response.ok) return [];
        return configuredProviders(await response.json());
    } catch {
        return [];
    }
}
