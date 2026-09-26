/** How This Mac → Overview describes the local search model.
 *
 *  Read from the backend's `/health/capabilities` (`capabilities.embeddings`).
 *  The model is downloaded once, on first run, so "Downloading…" is the state
 *  somebody sees on a fresh install, and "Couldn't download" the one they see
 *  offline — neither is a fault with their files, and the row says so.
 *  `disabled` (search embeds through a provider, not on this Mac) and anything
 *  unrecognised show no row at all.
 */
export interface SearchModelRow {
    state: "ready" | "busy" | "bad";
    value: string;
    consequence: string;
}

export function searchModelRow(status: string | null | undefined): SearchModelRow | null {
    switch (status) {
        case "ready":
            return { state: "ready", value: "Ready", consequence: "Files you add become searchable for your teammates." };
        case "preparing":
            return {
                state: "busy",
                value: "Downloading…",
                consequence: "Lemma is still downloading its search model (needs internet once). Files will become searchable when it finishes.",
            };
        case "degraded":
            return {
                state: "bad",
                value: "Couldn’t download — retries automatically",
                consequence: "Lemma needs internet once to download its search model. Your files are kept and become searchable when it succeeds.",
            };
        case "lazy":
            return { state: "busy", value: "Downloads when first needed", consequence: "Lemma downloads its search model the first time a file is added (needs internet once)." };
        default:
            return null;
    }
}

/** The embeddings status out of a `/health/capabilities` body, or null. */
export function embeddingsStatus(body: unknown): string | null {
    const status = (body as { capabilities?: { embeddings?: { status?: unknown } } } | null)?.capabilities?.embeddings?.status;
    return typeof status === "string" ? status : null;
}
