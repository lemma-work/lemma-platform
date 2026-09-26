/** What the "Connect a key" dialog sends, worked out away from the dialog.
 *
 *  Pure so the parts that are easy to get quietly wrong are tested: which
 *  models are offered a "reads images" tick, that a tick on a model no longer
 *  in the list is not sent, and that an Anthropic route sends none at all —
 *  every Claude model reads images, and the backend marks them so itself. */

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
