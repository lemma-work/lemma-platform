/**
 * Reading the backend's structured surface errors.
 *
 * A credential conflict is the one failure the setup UI must render as *state*
 * rather than a message: the shared Lemma bot/number is claimable once per org,
 * and a connected account binds to one surface. The catalog publishes that claim
 * up front so the option is disabled before the user commits — this parser is
 * the backstop for the race (someone else claimed it between the catalog read
 * and the save) and names the pod that won.
 */

export interface SurfaceCredentialConflict {
    /** SYSTEM = the shared Lemma bot/number. ACCOUNT = a connected account. */
    kind: 'SYSTEM' | 'ACCOUNT';
    podId: string | null;
    surfaceName: string | null;
    message: string;
}

function errorBody(error: unknown): Record<string, unknown> | null {
    if (!error || typeof error !== 'object') return null;
    // The generated client hangs the parsed envelope off `body`; some transports
    // surface it as `response`. Try both before giving up.
    for (const key of ['body', 'response'] as const) {
        const candidate = (error as Record<string, unknown>)[key];
        if (candidate && typeof candidate === 'object') return candidate as Record<string, unknown>;
    }
    return null;
}

export function parseCredentialConflict(error: unknown): SurfaceCredentialConflict | null {
    const body = errorBody(error);
    if (!body || body.code !== 'AGENT_SURFACE_CREDENTIAL_CONFLICT') return null;

    const details = (body.details ?? {}) as Record<string, unknown>;
    const conflicting = (details.conflicting_surface ?? {}) as Record<string, unknown>;
    const kind = details.kind === 'ACCOUNT' ? 'ACCOUNT' : 'SYSTEM';

    return {
        kind,
        podId: typeof conflicting.pod_id === 'string' ? conflicting.pod_id : null,
        surfaceName: typeof conflicting.name === 'string' ? conflicting.name : null,
        message:
            typeof body.message === 'string'
                ? body.message
                : 'Those credentials are already in use in this organization.',
    };
}

/** The human-facing message for any surface failure, conflict or not. */
export function surfaceErrorMessage(error: unknown, fallback: string): string {
    const conflict = parseCredentialConflict(error);
    if (conflict) return conflict.message;
    const body = errorBody(error);
    if (body && typeof body.message === 'string' && body.message.trim()) return body.message;
    if (error instanceof Error && error.message.trim()) return error.message;
    return fallback;
}
