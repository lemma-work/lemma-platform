import { describe, expect, it } from 'vitest';

import { blockedReason, findCatalogSurface, hasSystemIdentity, type CatalogSurface } from '@/lib/surfaces/catalog';

function entry(overrides: Record<string, unknown> = {}): CatalogSurface {
    return {
        platform: 'TELEGRAM',
        connector_id: 'telegram',
        provider: 'LEMMA',
        supported_credential_modes: ['CUSTOM', 'SYSTEM'],
        connector_available: true,
        ...overrides,
    } as unknown as CatalogSurface;
}

describe('surface catalog', () => {
    it('matches a platform regardless of casing', () => {
        const catalog = [entry(), entry({ platform: 'WHATSAPP', connector_id: 'whatsapp' })];
        expect(findCatalogSurface(catalog, 'whatsapp')?.connector_id).toBe('whatsapp');
        expect(findCatalogSurface(catalog, 'WHATSAPP')?.connector_id).toBe('whatsapp');
        expect(findCatalogSurface(catalog, 'discord')).toBeNull();
        expect(findCatalogSurface(undefined, 'telegram')).toBeNull();
    });

    it('reads the deployment, not the platform, for a Lemma-managed identity', () => {
        expect(hasSystemIdentity(entry())).toBe(true);
        expect(hasSystemIdentity(entry({ supported_credential_modes: ['CUSTOM'] }))).toBe(false);
    });

    describe('blocking an identity option', () => {
        it('lets both options through when nothing is in the way', () => {
            expect(blockedReason(entry(), 'SYSTEM')).toBeNull();
            expect(blockedReason(entry(), 'CUSTOM')).toBeNull();
        });

        it('blocks the shared identity when the deployment has none', () => {
            const blocked = blockedReason(entry({ supported_credential_modes: ['CUSTOM'] }), 'SYSTEM');
            // Says it's unavailable without naming a "deployment" — the word is
            // ours, not the reader's.
            expect(blocked?.reason).toMatch(/not available/i);
            expect(blocked?.reason).not.toMatch(/deployment|connector|credential mode/i);
        });

        it('names the pod that already claimed the shared identity', () => {
            const blocked = blockedReason(
                entry({
                    system_claim: {
                        available: false,
                        claimed_by_pod_id: 'pod-9',
                        claimed_by_surface_name: 'telegram',
                    },
                }),
                'SYSTEM',
            );
            expect(blocked?.reason).toMatch(/another pod/i);
            // The link is what turns a dead end into something actionable.
            expect(blocked?.claimedByPodId).toBe('pod-9');
        });

        it('treats an available claim as no block at all', () => {
            expect(blockedReason(entry({ system_claim: { available: true } }), 'SYSTEM')).toBeNull();
        });

        it('blocks bring-your-own when the connector is not configured here', () => {
            const blocked = blockedReason(entry({ connector_available: false }), 'CUSTOM');
            expect(blocked?.reason).toMatch(/isn’t set up here/i);
        });

        it('offers the managed hand-off only where a manager bot exists', () => {
            // Provisioning a bot for the user needs a manager bot on this
            // deployment; offering it otherwise dead-ends after they commit.
            expect(blockedReason(entry({ managed_setup_available: true }), 'MANAGED')).toBeNull();
            const blocked = blockedReason(entry({ managed_setup_available: false }), 'MANAGED');
            expect(blocked?.reason).toMatch(/not available/i);
            // Only an explicit false blocks: a backend predating the field would
            // otherwise hide the primary way to connect Telegram.
            expect(blockedReason(entry(), 'MANAGED')).toBeNull();
        });

        // A missing catalog means "not loaded yet", not "unavailable" — blocking
        // on absent data would make every option dead on first paint.
        it('does not block on a catalog that has not arrived', () => {
            expect(blockedReason(null, 'SYSTEM')).toBeNull();
            expect(blockedReason(null, 'CUSTOM')).toBeNull();
        });
    });
});
