import { describe, expect, it } from 'vitest';

import {
    forAgent,
    getSurfaceDefinition,
    SURFACE_PLATFORM_ORDER,
} from '@/lib/surfaces/registry';
import { POD_DEFAULT_AGENT_SELECTOR } from 'lemma-sdk';

import { DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';

describe('surface registry', () => {
    it('resolves a definition regardless of casing', () => {
        expect(getSurfaceDefinition('telegram')?.label).toBe('Telegram');
        expect(getSurfaceDefinition('TELEGRAM')?.label).toBe('Telegram');
        expect(getSurfaceDefinition('discord')).toBeNull();
        expect(getSurfaceDefinition(null)).toBeNull();
    });

    it('offers every registered platform exactly once, in display order', () => {
        expect(new Set(SURFACE_PLATFORM_ORDER).size).toBe(SURFACE_PLATFORM_ORDER.length);
        // The one-tap platforms lead: they are the ones a person can finish now.
        expect(SURFACE_PLATFORM_ORDER.slice(0, 2)).toEqual(['TELEGRAM', 'WHATSAPP']);
    });

    it('gives every platform the copy the modal renders', () => {
        for (const platform of SURFACE_PLATFORM_ORDER) {
            const definition = getSurfaceDefinition(platform);
            expect(definition, platform).not.toBeNull();
            expect(definition!.promise, platform).toContain('{agent}');
            expect(definition!.connectHint.length, platform).toBeGreaterThan(0);
        }
    });

    it('pairs every bring-your-own journey with a step that owns the input', () => {
        // A journey with no `field` step would render instructions and no way to
        // act on them.
        for (const platform of SURFACE_PLATFORM_ORDER) {
            const journey = getSurfaceDefinition(platform)!.journey;
            if (!journey) continue;
            expect(journey.steps.some((step) => step.field), platform).toBe(true);
        }
    });

    it('only offers a journey where there is a fork to reach it from', () => {
        for (const platform of SURFACE_PLATFORM_ORDER) {
            const definition = getSurfaceDefinition(platform)!;
            if (!definition.journey) continue;
            expect(definition.identityOptions, platform).not.toBeNull();
        }
    });

    it('offers no shared Telegram bot — every bot is the user’s own', () => {
        const telegram = getSurfaceDefinition('TELEGRAM')!;
        // Creating one leads; reusing a connected bot is the fallback. A SYSTEM
        // option would promise a shared bot that no longer exists, and a journey
        // would mean asking someone to make the bot by hand.
        expect(telegram.identityOptions?.map((option) => option.mode)).toEqual([
            'MANAGED',
            'CUSTOM',
        ]);
        expect(telegram.journey).toBeUndefined();
    });

    it('offers no identity fork where reach is a channel', () => {
        // A workspace is installed once and then carries many channels, so the
        // question "whose bot is this" has one answer and does not belong in the
        // journey. Slack's fork used to render a permanently disabled "Fastest"
        // option, because the catalog never reports a Lemma-managed Slack bot.
        for (const platform of SURFACE_PLATFORM_ORDER) {
            const definition = getSurfaceDefinition(platform)!;
            if (!definition.capabilities.channelRoutes) continue;
            expect(definition.identityOptions, platform).toBeNull();
        }
    });

    it('names the agent in second-person copy, and the default responder otherwise', () => {
        expect(forAgent('Make {agent} reachable', 'Ops')).toBe('Make Ops reachable');
        expect(forAgent('Make {agent} reachable', null)).toBe(`Make ${DEFAULT_RESPONDER_NAME} reachable`);
    });

    it('calls the pod assistant Lem when the surface arrives carrying its row name', () => {
        // `null` was the pod default only while it had no row. It has one now,
        // and `GET /surfaces` reports `agent_name` from that row -- so the
        // truthy string `pod_default` walks straight past `agentName || ...`
        // and onto the screen. The same falsy guard, at the same cost, as the
        // five backend sites #609 fixed.
        for (const stored of ['pod_default', POD_DEFAULT_AGENT_SELECTOR]) {
            expect(forAgent('Make {agent} reachable', stored)).toBe(
                `Make ${DEFAULT_RESPONDER_NAME} reachable`,
            );
        }
    });
});
