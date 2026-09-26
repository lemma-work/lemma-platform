/** What the "Connect a key" dialog sends, worked out away from the dialog.
 *
 *  Pure so the parts that are easy to get quietly wrong are tested: which
 *  models are offered a "reads images" tick, that a tick on a model no longer
 *  in the list is not sent, and that an Anthropic route sends none at all —
 *  every Claude model reads images, and the backend marks them so itself. */

import type { Choice, Runtime } from "@/data/runtimes";

export type ProviderProtocol = "openai" | "anthropic";

/** The comma-separated Models field, as names. Blank entries and repeats go. */
export function modelNames(text: string): string[] {
    const names: string[] = [];
    for (const raw of text.split(",")) {
        const name = raw.trim();
        if (name && !names.includes(name)) names.push(name);
    }
    return names;
}

/** The models a person can mark as reading images.
 *
 *  What they typed, when they typed anything; otherwise what a Test found.
 *  An empty Models field means "the route's own list", so the tested list is
 *  the same list the backend will discover on save. */
export function visionCandidates(typed: string[], discovered: string[]): string[] {
    return typed.length > 0 ? typed : discovered;
}

/** Whether this protocol needs people to say which models read images.
 *
 *  An OpenAI-compatible `/models` list mostly says nothing about modalities,
 *  and a text-only model handed an image breaks the conversation, so the
 *  backend treats silence as "cannot". Anthropic's models all can. */
export function asksAboutImages(protocol: ProviderProtocol): boolean {
    return protocol === "openai";
}

/** The ticks worth sending: only ones on models still on offer. */
export function chosenVisionModels(
    protocol: ProviderProtocol,
    ticked: string[],
    candidates: string[],
): string[] {
    if (!asksAboutImages(protocol)) return [];
    return ticked.filter((name) => candidates.includes(name));
}

/** The request This Mac's model lookup takes, for a route and key typed into
 *  the dialog. The key is sent as typed — even empty — because an omitted
 *  one tells the lookup to attach the key stored on this computer, which is
 *  the wrong key for a different route. */
export function discoveryRequest(protocol: ProviderProtocol, baseUrl: string, apiKey: string): Record<string, unknown> {
    return {
        ai: {
            protocol: protocol === "anthropic" ? "anthropic_compat" : "openai_compat",
            base_url: baseUrl.trim(),
            default_model: "",
            models: [],
            vision_models: [],
            allow_private_network: false,
        },
        api_key: apiKey.trim(),
    };
}

/** Model names out of whatever the lookup answered with. */
export function readDiscoveredModels(answer: unknown): string[] {
    if (!Array.isArray(answer)) return [];
    const names: string[] = [];
    for (const entry of answer) {
        const name = typeof entry === "string"
            ? entry
            : entry && typeof entry === "object" && "id" in entry && typeof entry.id === "string"
                ? entry.id
                : entry && typeof entry === "object" && "name" in entry && typeof entry.name === "string"
                    ? entry.name
                    : "";
        const trimmed = name.trim();
        if (trimmed && !names.includes(trimmed)) names.push(trimmed);
    }
    return names;
}

const LOOPBACK_URL = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i;

/** Whether a route is a model server on this computer. */
export function isLocalRoute(baseUrl: string | null | undefined): boolean {
    return LOOPBACK_URL.test((baseUrl ?? "").trim());
}

function sameRoute(a: string, b: string): boolean {
    const plain = (url: string) => url.trim().replace(/\/+$/, "").toLowerCase().replace("://localhost", "://127.0.0.1");
    return plain(a) === plain(b);
}

/** Whether a saved provider on this computer is answering right now.
 *
 *  `null` when there is nothing to say: not a local route, or the check has
 *  not run. A local route the check did not find is not answering — Ollama
 *  or LM Studio was quit, and every run on it will be refused until it is
 *  started again, which the row should say before a message does. */
export function localRouteAnswering(
    baseUrl: string | null | undefined,
    found: { baseUrl: string }[] | undefined,
): boolean | null {
    if (!isLocalRoute(baseUrl) || !found) return null;
    return found.some((server) => sameRoute(server.baseUrl, baseUrl ?? ""));
}

/** The key sent for a route that takes none. The profile schema wants one;
 *  a local model server ignores it. Matches `LOCAL_SERVER_KEY`. */
export function keyToSend(apiKey: string, baseUrl: string, localKey: string): string {
    const typed = apiKey.trim();
    return typed || (isLocalRoute(baseUrl) ? localKey : "");
}

/** A Test that failed, said as what to do about it. The lookup's own text is
 *  read only to tell a refused key from everything else. */
export function testFailureMessage(provider: string, reason: string): string {
    if (/\b(401|403)\b|unauthori[sz]ed|forbidden|invalid[ _-]?api[ _-]?key|rejected/i.test(reason)) {
        return provider + " rejected this API key.";
    }
    return "Couldn't read the model list from " + provider + ". Check the key, or type a model name below.";
}

/** Whether a key can be the organization's default: organization-wide, not
 *  retired, a provider rather than a coding agent. Matches the backend's
 *  `can_be_organization_default`, so the page never offers a button that
 *  would be refused. */
export function canBeOrganizationDefault(runtime: Runtime): boolean {
    return runtime.kind === "key" && runtime.scope === "org" && !runtime.archived;
}

/** The key to offer as everyone's model right after it was added, or `null`.
 *
 *  Only for the very first one: when nothing could answer before (no live
 *  runtime at all, the server's own model included) and nobody has chosen a
 *  default. Later keys are a choice between models, which "Make default" on
 *  the row already offers; asking again on every add would nag. */
export function firstProviderOffer(
    before: Runtime[],
    after: Runtime[],
    organizationDefault: Choice | null,
): Runtime | null {
    if (organizationDefault) return null;
    if (before.some((runtime) => !runtime.archived)) return null;
    const known = new Set(before.map((runtime) => runtime.id));
    return after.find((runtime) => !known.has(runtime.id) && canBeOrganizationDefault(runtime)) ?? null;
}
