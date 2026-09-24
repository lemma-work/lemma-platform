/** History fetching and a teammate's reply are independent kinds of progress. */
export function transcriptState({ loading, hasTurns, hasStreamingText, error }: {
    loading: boolean;
    hasTurns: boolean;
    hasStreamingText: boolean;
    error: string | null;
}): "loading" | "empty" | "content" | "error" {
    if (hasTurns || hasStreamingText) return "content";
    if (loading) return "loading";
    if (error) return "error";
    return "empty";
}
