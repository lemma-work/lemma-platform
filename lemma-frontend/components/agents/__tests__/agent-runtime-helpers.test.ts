import { describe, expect, it } from 'vitest';
import { HarnessKind, RuntimeProfileKind, RuntimeProfileProtocol, RuntimeProfileScope, RuntimeProfileStatus } from 'lemma-sdk';
import {
    CODING_AGENT_KINDS,
    HARNESS_LOGOS,
    LOCAL_RUNTIME_SETUP_OPTIONS,
    firstRuntime,
    isCodingAgentKind,
    resolveDefaultAgentRuntime,
    resolveGG_CODERDefaultRuntime,
    runtimeCatalogToModelOptions,
    shortModelName,
} from '@/components/agents/agent-runtime-helpers';

function makeProfile(
    overrides: Record<string, unknown> = {},
): Record<string, unknown> {
    return {
        id: 'ggc',
        organization_id: 'org',
        user_id: 'u',
        daemon_id: 'd',
        scope: RuntimeProfileScope.PERSONAL,
        kind: RuntimeProfileKind.HARNESS,
        protocol: RuntimeProfileProtocol.GG_CODER,
        name: 'GG Coder',
        description: null,
        default_model_name: 'default',
        model_catalog: [],
        config: {},
        status: RuntimeProfileStatus.ACTIVE,
        metadata: {},
        has_credentials: false,
        derived_harness_kind: HarnessKind.GG_CODER,
        daemon_display_name: null,
        daemon_status: null,
        daemon_harness_available: null,
        availability_status: 'READY' as const,
        ...overrides,
    };
}

function makeHarness(
    overrides: Record<string, unknown> = {},
): Record<string, unknown> {
    return {
        harness_kind: HarnessKind.GG_CODER,
        display_name: 'GG Coder',
        models: ['default'],
        model_catalog: [],
        available: true,
        availability_status: 'READY' as const,
        daemon_id: 'd',
        daemon_display_name: 'Workstation',
        daemon_status: 'ONLINE' as const,
        ...overrides,
    };
}

describe('agent-runtime-helpers / GG Coder', () => {
    it('classifies GG_CODER as a local coding agent', () => {
        expect(isCodingAgentKind(HarnessKind.GG_CODER)).toBe(true);
        expect(CODING_AGENT_KINDS.has(HarnessKind.GG_CODER)).toBe(true);
    });

    it('still classifies the legacy harnesses as coding agents', () => {
        for (const kind of [
            HarnessKind.CLAUDE_CODE,
            HarnessKind.CODEX,
            HarnessKind.OPENCODE,
            HarnessKind.ANTIGRAVITY,
            HarnessKind.CURSOR,
        ]) {
            expect(isCodingAgentKind(kind)).toBe(true);
        }
    });

    it('exposes a logo for every supported coding agent', () => {
        for (const kind of CODING_AGENT_KINDS) {
            expect(HARNESS_LOGOS[kind], `missing logo for ${kind}`).toBeTruthy();
        }
    });

    it('lists GG Coder first in the setup menu (default chat)', () => {
        expect(LOCAL_RUNTIME_SETUP_OPTIONS[0]?.harnessKind).toBe(HarnessKind.GG_CODER);
        expect(LOCAL_RUNTIME_SETUP_OPTIONS[0]?.title).toBe('GG Coder');
    });

    it('resolveDefaultAgentRuntime prefers the catalog default', () => {
        const runtime = { profile_id: 'system:gg-coder', model_name: 'default' };
        expect(
            resolveDefaultAgentRuntime({
                items: [],
                default_runtime: runtime,
            } as never),
        ).toEqual(runtime);
    });

    it('resolveDefaultAgentRuntime resolves a USER_DAEMON GG_CODER profile by id', () => {
        const profile = {
            id: 'ggc',
            organization_id: 'org',
            user_id: 'u',
            daemon_id: 'd',
            scope: RuntimeProfileScope.PERSONAL,
            kind: RuntimeProfileKind.HARNESS,
            protocol: RuntimeProfileProtocol.GG_CODER,
            name: 'GG Coder',
            description: null,
            default_model_name: 'default',
            model_catalog: [],
            config: {},
            status: RuntimeProfileStatus.ACTIVE,
            metadata: {},
            has_credentials: false,
            derived_harness_kind: HarnessKind.GG_CODER,
            daemon_display_name: null,
            daemon_status: null,
            daemon_harness_available: null,
            availability_status: 'READY' as const,
        };
        const result = resolveDefaultAgentRuntime(
            { items: [profile], default_runtime: null } as never,
            'ggc',
        );
        expect(result?.profile_id).toBe('ggc');
        expect(result?.model_name).toBe('default');
    });

    it('firstRuntime returns the catalog default when present', () => {
        const runtime = { profile_id: 'p', model_name: 'm' };
        expect(
            firstRuntime({ items: [], default_runtime: runtime } as never),
        ).toEqual(runtime);
    });

    it('shortModelName keeps only the last path segment', () => {
        expect(shortModelName('claude-opus-4-8')).toBe('claude-opus-4-8');
        expect(shortModelName('opencode/deepseek-v4-flash-free')).toBe('deepseek-v4-flash-free');
    });

    it('runtimeCatalogToModelOptions maps profiles into model rows', () => {
        const options = runtimeCatalogToModelOptions(
            {
                items: [
                    {
                        id: 'ggc',
                        organization_id: 'org',
                        user_id: 'u',
                        daemon_id: 'd',
                        scope: RuntimeProfileScope.PERSONAL,
                        kind: RuntimeProfileKind.HARNESS,
                        protocol: RuntimeProfileProtocol.GG_CODER,
                        name: 'GG Coder',
                        description: null,
                        default_model_name: 'default',
                        model_catalog: [
                            {
                                name: 'default',
                                display_name: 'Default',
                                provider_model_name: 'default',
                                capabilities: [],
                                default_model_settings: {},
                                metadata: {},
                            },
                        ],
                        config: {},
                        status: RuntimeProfileStatus.ACTIVE,
                        metadata: {},
                        has_credentials: false,
                        derived_harness_kind: HarnessKind.GG_CODER,
                        daemon_display_name: null,
                        daemon_status: null,
                        daemon_harness_available: null,
                        availability_status: 'READY',
                    },
                ],
                default_runtime: null,
            } as never,
            undefined,
        );
        expect(options).toHaveLength(1);
        expect(options[0]?.id).toBe('default');
    });

    describe('resolveGG_CODERDefaultRuntime', () => {
        it('returns null when the catalog is missing', () => {
            expect(resolveGG_CODERDefaultRuntime(undefined, undefined)).toBeNull();
        });

        it('picks the catalog default when the harness is available', () => {
            const runtime = { profile_id: 'sys', model_name: 'default' };
            const result = resolveGG_CODERDefaultRuntime(
                {
                    items: [makeProfile({ id: 'sys' })],
                    default_runtime: runtime,
                } as never,
                { items: [makeHarness()] } as never,
            );
            expect(result).toEqual(runtime);
        });

        it('picks the GG_CODER USER_DAEMON profile when no default is set', () => {
            const result = resolveGG_CODERDefaultRuntime(
                { items: [makeProfile({ id: 'ggc' })], default_runtime: null } as never,
                { items: [makeHarness()] } as never,
            );
            expect(result?.profile_id).toBe('ggc');
            expect(result?.model_name).toBe('default');
        });

        it('prefers GG_CODER over other detected coding-agent profiles', () => {
            const claudeProfile = makeProfile({
                id: 'claude',
                protocol: RuntimeProfileProtocol.CLAUDE_CODE,
                derived_harness_kind: HarnessKind.CLAUDE_CODE,
                name: 'Claude Code',
            });
            const result = resolveGG_CODERDefaultRuntime(
                { items: [claudeProfile, makeProfile({ id: 'ggc' })], default_runtime: null } as never,
                {
                    items: [
                        makeHarness({ harness_kind: HarnessKind.CLAUDE_CODE, daemon_id: 'd1' }),
                        makeHarness({ harness_kind: HarnessKind.GG_CODER, daemon_id: 'd2' }),
                    ],
                } as never,
            );
            expect(result?.profile_id).toBe('ggc');
        });

        it('falls back to any detected coding-agent when GG_CODER is unavailable', () => {
            const claudeProfile = makeProfile({
                id: 'claude',
                protocol: RuntimeProfileProtocol.CLAUDE_CODE,
                derived_harness_kind: HarnessKind.CLAUDE_CODE,
                name: 'Claude Code',
            });
            const result = resolveGG_CODERDefaultRuntime(
                { items: [claudeProfile], default_runtime: null } as never,
                {
                    items: [
                        makeHarness({
                            harness_kind: HarnessKind.CLAUDE_CODE,
                            daemon_id: 'd1',
                        }),
                    ],
                } as never,
            );
            expect(result?.profile_id).toBe('claude');
        });

        it('returns null when no profile is detected/available', () => {
            const claudeProfile = makeProfile({
                id: 'claude',
                protocol: RuntimeProfileProtocol.CLAUDE_CODE,
                derived_harness_kind: HarnessKind.CLAUDE_CODE,
                name: 'Claude Code',
            });
            const result = resolveGG_CODERDefaultRuntime(
                { items: [claudeProfile], default_runtime: null } as never,
                {
                    // ggcoder not installed, claude not detected: nobody
                    // is available right now.
                    items: [],
                } as never,
            );
            expect(result).toBeNull();
        });
    });
});
