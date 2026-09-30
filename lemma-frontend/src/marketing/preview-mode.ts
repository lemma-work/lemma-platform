/** A separate document, never a query switch on the signed-in workspace. */
export const PREVIEW_PATHS = ["/demo/landing", "/demo/launch"] as const;

export function isPreviewPath(path: string): boolean {
    return PREVIEW_PATHS.some(allowed => path === allowed || path === allowed + "/");
}

export function isLandingPreview(): boolean {
    return typeof window !== "undefined" && isPreviewPath(window.location.pathname);
}

export function readTourStep(data: unknown): number | null {
    if (!data || typeof data !== "object") return null;
    const message = data as { type?: unknown; step?: unknown };
    return message.type === "lemma-tour:step" && Number.isInteger(message.step) &&
        typeof message.step === "number" && message.step >= -1 && message.step < 5 ? message.step : null;
}

export function previewTabForStep(step: number): string {
    /* "It learns how you work": the teammate's own page, open at what it has
       been taught. */
    if (step === 2) return "space:about";
    if (step === 3) return "app:launch";
    return "conversation";
}
