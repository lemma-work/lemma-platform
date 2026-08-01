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
    harnessConfigControls,
    hydrateRuntimeModel,
    isArchivedProfile,
    pairingCommands,
    resolveDefaultAgentRuntime,
    resolveRuntimeModelName,
    runtimeAvailabilityLabel,
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

describe('pairing commands', () => {
    const pairing = { pairing_code: 'code-123', display_name: 'Ana"s laptop' };

    it('does not ask for a separate install step', () => {
        // `connect` resolves the binary itself, and `install` only ever downloads
        // a release asset - which does not exist for a self-hosted or dev build,
        // so the first line of the old instructions stopped those users outright.
        expect(pairingCommands(pairing, 'https://api.lemma.work').join('\n')).not.toContain(
            'agent-host install',
        );
    });

    it('installs the CLI, pairs, then verifies', () => {
        expect(pairingCommands(pairing, 'https://api.lemma.work')).toEqual([
            'uv tool install lemma-terminal',
            'lemma agent-host connect --url https://api.lemma.work --pairing-code code-123 --name "Ana\\"s laptop"',
            'lemma agent-host status',
        ]);
    });

    it('opts in to plain HTTP only when the CLI would refuse the URL', () => {
        const connectFor = (apiBaseUrl: string) => pairingCommands(pairing, apiBaseUrl)[1];

        expect(connectFor('http://10.0.0.4:8710')).toContain('--allow-insecure-http');
        // Loopback is already trusted, so the flag would be noise.
        expect(connectFor('http://127.0.0.1:8710')).not.toContain('--allow-insecure-http');
        expect(connectFor('http://localhost:8710')).not.toContain('--allow-insecure-http');
        expect(connectFor('https://api.example.com')).not.toContain('--allow-insecure-http');
    });
});

describe('runtimeAvailabilityLabel', () => {
    // Until the backend started populating availability_status this always
    // returned null, so an offline machine's profile looked exactly like a
    // healthy one. Every state the API can send needs a name here.
    const harnessProfile = (availability: string | null) =>
        profile({
            id: 'org:codex',
            kind: RuntimeProfileKind.HARNESS,
            protocol: RuntimeProfileProtocol.AGENT_HOST,
            scope: RuntimeProfileScope.ORGANIZATION,
            harness_id: 'harness-1',
            availability_status: availability,
        });

    it('says nothing when the agent is ready', () => {
        expect(runtimeAvailabilityLabel(harnessProfile('READY'))).toBeNull();
    });

    it('names every unavailable state', () => {
        expect(runtimeAvailabilityLabel(harnessProfile('OFFLINE'))).toBe('Offline');
        expect(runtimeAvailabilityLabel(harnessProfile('NOT_INSTALLED'))).toBe('Not installed');
        expect(runtimeAvailabilityLabel(harnessProfile('UNAVAILABLE'))).toBe('Unavailable');
        expect(runtimeAvailabilityLabel(harnessProfile('UNAVAILABLE_FOR_YOU'))).toBe('Unavailable');
    });

    it('reports nothing for a provider profile, which is always reachable', () => {
        // The short-circuit on harness_id — never exercised before, because the
        // field was null on every profile the UI had ever seen.
        expect(runtimeAvailabilityLabel(profile({ id: 'org:byo', availability_status: 'OFFLINE' })))
            .toBeNull();
    });

    it('stays quiet on a status this build does not know', () => {
        expect(runtimeAvailabilityLabel(harnessProfile('SOMETHING_NEW'))).toBeNull();
        expect(runtimeAvailabilityLabel(harnessProfile(null))).toBeNull();
    });
});

describe('isArchivedProfile', () => {
    it('is what separates the catalog from the management listing', () => {
        expect(isArchivedProfile(profile({ id: 'a', status: RuntimeProfileStatus.DISABLED }))).toBe(true);
        expect(isArchivedProfile(profile({ id: 'b' }))).toBe(false);
    });
});

describe('harnessConfigControls', () => {
    it('renders a control only for options that enumerate their values', () => {
        // A free-text box here is how a policy-bearing value like
        // bypassPermissions would save cleanly and then be refused by the host
        // at session setup — a failure the user only sees on their first run.
        const controls = harnessConfigControls([
            {
                id: 'permission_mode',
                category: 'permission',
                name: 'Permission mode',
                description: 'How much the agent may do unattended',
                current_value: 'ask',
                options: [
                    { id: 'ask', name: 'Ask every time', value: 'ask' },
                    { id: 'plan', name: 'Plan only', value: 'plan' },
                ],
            },
            { id: 'workdir', category: 'path', name: 'Working directory', options: [] },
            { id: 'notes', category: 'misc', name: 'Notes' },
        ]);

        expect(controls.map((control) => control.id)).toEqual(['permission_mode']);
        expect(controls[0].choices).toEqual([
            { value: 'ask', label: 'Ask every time' },
            { value: 'plan', label: 'Plan only' },
        ]);
        expect(controls[0].currentValue).toBe('ask');
    });

    it('drops an escalating value from an option that enumerates it', () => {
        // Claude Code lists bypassPermissions among its own permission modes,
        // and Agent Host refuses it anyway at session setup. Offering it would
        // be a choice that can only ever fail on the user's first run.
        const [control] = harnessConfigControls([
            {
                id: 'permission_mode',
                category: 'permission',
                name: 'Permission mode',
                options: [
                    { id: 'default', name: 'Ask' },
                    { id: 'plan', name: 'Plan' },
                    { id: 'bypassPermissions', name: 'Bypass' },
                    { id: 'acceptEdits', name: 'Accept edits' },
                ],
            },
        ]);

        expect(control.choices.map((choice) => choice.value)).toEqual(['default', 'plan']);
    });

    it('leaves an ordinary option list alone', () => {
        // The filter keys off the option being policy-bearing, so a value that
        // merely looks alarming elsewhere is untouched.
        const [control] = harnessConfigControls([
            {
                id: 'startup',
                category: 'lifecycle',
                name: 'Startup',
                options: [{ id: 'auto' }, { id: 'manual' }],
            },
        ]);

        expect(control.choices.map((choice) => choice.value)).toEqual(['auto', 'manual']);
    });

    it('drops the model category, which default_model_name owns', () => {
        // Mirrors validate_agent_host_selections, which rejects a `model`
        // selection outright rather than quietly ignoring it.
        expect(
            harnessConfigControls([
                {
                    id: 'model',
                    category: 'model',
                    name: 'Model',
                    options: [{ id: 'gpt-5.1', value: 'gpt-5.1' }],
                },
            ]),
        ).toEqual([]);
    });

    it('keys by category when an option has no id, as the backend does', () => {
        const [control] = harnessConfigControls([
            {
                category: 'reasoning',
                name: 'Thinking effort',
                options: [{ id: 'low' }, { id: 'high', value: 'high' }],
            },
        ]);

        expect(control.selectionKey).toBe('reasoning');
        // `item.value ?? item.id` — the same fallback the backend allows.
        expect(control.choices).toEqual([
            { value: 'low', label: 'low' },
            { value: 'high', label: 'high' },
        ]);
    });

    it('ignores a current_value that is not one of the choices', () => {
        const [control] = harnessConfigControls([
            {
                id: 'effort',
                category: 'reasoning',
                name: 'Effort',
                current_value: 'ludicrous',
                options: [{ id: 'low', value: 'low' }],
            },
        ]);

        expect(control.currentValue).toBeNull();
    });

    it('survives a harness that publishes nothing', () => {
        expect(harnessConfigControls(undefined)).toEqual([]);
        expect(harnessConfigControls(null)).toEqual([]);
        expect(harnessConfigControls([])).toEqual([]);
    });
});
