import { describe, expect, it } from 'vitest';
import {
    HarnessKind,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
} from 'lemma-sdk';
import type {
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
} from 'lemma-sdk';

import {
    formatAgentRuntime,
    hydrateRuntimeModel,
    resolveDefaultAgentRuntime,
    resolveRuntimeModelName,
} from './agent-runtime-helpers';

function profile(
    overrides: Partial<AgentRuntimeProfileResponse> & { id: string },
): AgentRuntimeProfileResponse {
    return {
        scope: RuntimeProfileScope.SYSTEM,
        kind: RuntimeProfileKind.MODEL_PROVIDER,
        protocol: RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name: 'Lemma',
        default_model_name: null,
        model_catalog: [],
        config: {},
        status: RuntimeProfileStatus.ACTIVE,
        metadata: {},
        has_credentials: true,
        derived_harness_kind: HarnessKind.LEMMA,
        ...overrides,
    };
}

function model(name: string) {
    return {
        name,
        display_name: null,
        provider_model_name: name,
        capabilities: [],
        default_model_settings: {},
        metadata: {},
    };
}

// The shape the backend actually returns: `default_runtime` names the system
// profile and nothing else, because AgentRuntimeDefaultService.get_default()
// builds it from a profile id alone.
const catalog: AgentRuntimeProfileListResponse = {
    items: [
        profile({
            id: 'system:lemma',
            default_model_name: 'openai/gpt-5.1',
            model_catalog: [model('openai/gpt-5.1'), model('openai/gpt-5.1-mini')],
        }),
        profile({
            id: 'org:byo',
            name: 'Acme',
            scope: RuntimeProfileScope.ORGANIZATION,
            // No pinned default — the backend falls to the first catalog entry.
            model_catalog: [model('claude-sonnet-5'), model('claude-opus-5')],
        }),
    ],
    default_runtime: { profile_id: 'system:lemma' },
};

describe('resolveRuntimeModelName', () => {
    it('keeps an explicitly pinned model', () => {
        expect(resolveRuntimeModelName(
            { profile_id: 'system:lemma', model_name: 'openai/gpt-5.1-mini' },
            catalog,
        )).toBe('openai/gpt-5.1-mini');
    });

    it("names the profile's default model when the runtime pins only a profile", () => {
        expect(resolveRuntimeModelName({ profile_id: 'system:lemma' }, catalog))
            .toBe('openai/gpt-5.1');
    });

    it('falls back to the first catalog entry, as the backend does at dispatch', () => {
        expect(resolveRuntimeModelName({ profile_id: 'org:byo' }, catalog))
            .toBe('claude-sonnet-5');
    });

    it('names nothing when the catalog is absent or the profile has gone away', () => {
        expect(resolveRuntimeModelName({ profile_id: 'system:lemma' }, undefined)).toBeNull();
        expect(resolveRuntimeModelName({ profile_id: 'deleted' }, catalog)).toBeNull();
        expect(resolveRuntimeModelName(null, catalog)).toBeNull();
    });
});

describe('resolveDefaultAgentRuntime', () => {
    // The composer's own case: a pod that never set a default falls through to
    // the catalog's bare default_runtime, which used to reach the UI unnamed.
    it('names a model for a pod with no default of its own', () => {
        expect(resolveDefaultAgentRuntime(catalog, undefined)).toEqual({
            profile_id: 'system:lemma',
            model_name: 'openai/gpt-5.1',
        });
    });

    it('resolves the legacy provider-only pod default through its profile', () => {
        expect(resolveDefaultAgentRuntime(catalog, 'org:byo')).toEqual({
            profile_id: 'org:byo',
            model_name: 'claude-sonnet-5',
        });
    });

    it('falls back to the catalog default when the pinned profile is gone', () => {
        expect(resolveDefaultAgentRuntime(catalog, 'deleted')).toEqual({
            profile_id: 'system:lemma',
            model_name: 'openai/gpt-5.1',
        });
    });

    it('resolves nothing while the catalog is still loading', () => {
        expect(resolveDefaultAgentRuntime(undefined, 'system:lemma')).toBeNull();
    });
});

describe('hydrateRuntimeModel', () => {
    it('fills in the model an agent runtime leaves open', () => {
        expect(hydrateRuntimeModel({ profile_id: 'org:byo' }, catalog))
            .toEqual({ profile_id: 'org:byo', model_name: 'claude-sonnet-5' });
    });

    it('returns the runtime untouched when nothing can name its model', () => {
        const runtime = { profile_id: 'deleted' };
        expect(hydrateRuntimeModel(runtime, catalog)).toBe(runtime);
        expect(hydrateRuntimeModel(null, catalog)).toBeNull();
    });
});

describe('formatAgentRuntime', () => {
    it("resolves through the runtime's own profile, not the catalog default", () => {
        expect(formatAgentRuntime({ profile_id: 'org:byo' }, catalog))
            .toBe('Acme · claude-sonnet-5');
    });
});
