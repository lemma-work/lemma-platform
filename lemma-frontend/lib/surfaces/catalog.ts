import type { AvailableSurface, SurfaceCredentialMode } from 'lemma-sdk';

import type { SurfaceIdentityMode } from '@/lib/surfaces/registry';

/**
 * Deployment-specific truths about a surface platform, read from
 * `agent.surface.available`.
 *
 * `system_claim` is newer than the checked-in generated client, so it is
 * declared here rather than imported. Regenerating the SDK against a backend
 * that carries it makes `CatalogSurface` collapse back into `AvailableSurface`.
 */
export interface SurfaceSystemClaim {
    available: boolean;
    claimed_by_pod_id?: string | null;
    claimed_by_surface_name?: string | null;
}

export type CatalogSurface = AvailableSurface & {
    system_claim?: SurfaceSystemClaim | null;
    managed_setup_available?: boolean;
};

export function findCatalogSurface(
    catalog: CatalogSurface[] | undefined,
    platform: string | null | undefined,
): CatalogSurface | null {
    if (!catalog || !platform) return null;
    const target = platform.toUpperCase();
    return catalog.find((entry) => String(entry.platform).toUpperCase() === target) ?? null;
}

function supportsMode(entry: CatalogSurface | null, mode: SurfaceCredentialMode): boolean {
    if (!entry) return false;
    return (entry.supported_credential_modes ?? []).some(
        (candidate) => String(candidate).toUpperCase() === mode,
    );
}

/** A Lemma-managed bot/number exists in this deployment at all. */
export function hasSystemIdentity(entry: CatalogSurface | null): boolean {
    return supportsMode(entry, 'SYSTEM' as SurfaceCredentialMode);
}

/**
 * This deployment can provision a dedicated bot for the user (Telegram).
 *
 * Only an explicit `false` blocks. A backend that predates the field reports
 * nothing, and treating that silence as "unavailable" would hide the primary
 * way to connect Telegram — the same reason an unloaded catalog and an
 * unreachable credential check don't block either. The server still refuses if
 * it really has no manager bot, and that refusal renders inline.
 */
export function hasManagedSetup(entry: CatalogSurface | null): boolean {
    return entry?.managed_setup_available !== false;
}

/** Why an identity option can't be picked right now, or null when it can. */
export interface IdentityBlock {
    reason: string;
    /** A pod in the caller's own org that holds the claim. */
    claimedByPodId?: string | null;
}

export function blockedReason(
    entry: CatalogSurface | null,
    mode: SurfaceIdentityMode,
): IdentityBlock | null {
    if (!entry) return null;

    if (mode === 'SYSTEM') {
        if (!hasSystemIdentity(entry)) {
            return { reason: 'Not available here.' };
        }
        const claim = entry.system_claim;
        if (claim && claim.available === false) {
            return {
                // Named rather than "another pod" — the holder is always in the
                // caller's own org, so there is nothing to withhold.
                reason: 'Another pod is already using it.',
                claimedByPodId: claim.claimed_by_pod_id ?? null,
            };
        }
        return null;
    }

    if (mode === 'MANAGED' && !hasManagedSetup(entry)) {
        return { reason: 'Not available here.' };
    }

    if (!entry.connector_available) {
        return { reason: 'This isn’t set up here yet.' };
    }
    return null;
}

/** The credential fields a bring-your-own account needs, from the connector. */
export function credentialSchema(entry: CatalogSurface | null): Record<string, unknown> | null {
    const schema = entry?.connect?.credential_schema;
    return schema && typeof schema === 'object' ? (schema as Record<string, unknown>) : null;
}
