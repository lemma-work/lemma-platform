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
    HARNESS_DEFAULT_VALUE,
    formatAgentRuntime,
    canConfigureHarnessProfile,
    harnessConfigControls,
    harnessProfileChanges,
    hydrateRuntimeModel,
    isLocalAgentSignInFailure,
    isArchivedProfile,
    isDiscoveringHarnesses,
    HARNESS_DISCOVERY_WINDOW_MS,
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

describe('harness discovery', () => {
    const host = (overrides: Partial<{ status: string; created_at: string }> = {}) => ({
        status: 'ONLINE',
        created_at: new Date().toISOString(),
        ...overrides,
    }) as Parameters<typeof isDiscoveringHarnesses>[0];

    it('treats an empty list on a freshly paired computer as still looking', () => {
        // The reported bug: the page concluded "No agents published yet" within
        // two seconds of the first empty response, while the host was still
        // installing an adapter package per agent against an empty npm cache.
        expect(isDiscoveringHarnesses(host(), 0)).toBe(true);
    });

    it('stops looking once the computer has published something', () => {
        expect(isDiscoveringHarnesses(host(), 1)).toBe(false);
    });

    it('stops looking once discovery has had long enough', () => {
        const old = new Date(Date.now() - HARNESS_DISCOVERY_WINDOW_MS - 1_000).toISOString();
        expect(isDiscoveringHarnesses(host({ created_at: old }), 0)).toBe(false);
    });

    it('covers the host\'s own connect timeout, which is what makes it slow', () => {
        // agent-host's CONNECT_TIMEOUT is 600s; a window shorter than that
        // would call discovery finished while the host was still working.
        expect(HARNESS_DISCOVERY_WINDOW_MS).toBeGreaterThanOrEqual(600_000);
    });

    it('never claims a revoked computer is still looking', () => {
        expect(isDiscoveringHarnesses(host({ status: 'REVOKED' }), 0)).toBe(false);
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

describe('canConfigureHarnessProfile', () => {
    it('lets the model and settings be changed on a reachable computer', () => {
        expect(canConfigureHarnessProfile({ availability_status: 'READY' })).toBe(true);
    });

    it('withholds them while that computer cannot be reached', () => {
        // The backend validates a model or config change against what the
        // harness advertises right now, so an offline machine cannot take one.
        // Renaming is unaffected and stays available in the dialog.
        expect(canConfigureHarnessProfile({ availability_status: 'OFFLINE' })).toBe(false);
        expect(canConfigureHarnessProfile({ availability_status: 'NOT_INSTALLED' })).toBe(false);
        expect(canConfigureHarnessProfile({ availability_status: 'UNAVAILABLE' })).toBe(false);
    });

    it('treats an unreported status as usable rather than guessing offline', () => {
        // Two backend call sites build the service without a host repository and
        // leave this null. Reading that as offline would disable a control the
        // user can in fact save.
        expect(canConfigureHarnessProfile({ availability_status: null })).toBe(true);
        expect(canConfigureHarnessProfile({})).toBe(true);
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

describe('harnessProfileChanges', () => {
    const stored = {
        name: 'Codex',
        description: 'Repo work',
        defaultModel: 'gpt-5.1',
        selections: { permission_mode: 'plan' },
    };

    it('sends nothing when nothing moved', () => {
        expect(harnessProfileChanges(stored, { ...stored })).toEqual({});
    });

    it('keeps a rename off the harness path', () => {
        // The backend contacts the paired computer only when an edit touches
        // default_model_name or config_selections. Including them here would
        // make renaming a coding agent fail whenever that laptop is asleep.
        const changes = harnessProfileChanges(stored, { ...stored, name: 'Codex (main)' });

        expect(changes).toEqual({ name: 'Codex (main)' });
        expect(changes).not.toHaveProperty('default_model_name');
        expect(changes).not.toHaveProperty('config_selections');
    });

    it('clears a description the user emptied, rather than omitting it', () => {
        expect(harnessProfileChanges(stored, { ...stored, description: '   ' }))
            .toEqual({ description: null });
    });

    it('maps the sentinel back to null when unpinning the model', () => {
        expect(
            harnessProfileChanges(stored, { ...stored, defaultModel: HARNESS_DEFAULT_VALUE }),
        ).toEqual({ default_model_name: null });
    });

    it('ignores a selection left on the sentinel', () => {
        // Rendering a control defaults it to "use this computer's setting",
        // which is the absence of a selection - not a change to send.
        expect(
            harnessProfileChanges(stored, {
                ...stored,
                selections: { permission_mode: 'plan', reasoning: HARNESS_DEFAULT_VALUE },
            }),
        ).toEqual({});
    });

    it('sends the whole selection map when one entry changes', () => {
        // Selections replace wholesale server-side, so a partial map would drop
        // the others.
        expect(
            harnessProfileChanges(stored, {
                ...stored,
                selections: { permission_mode: 'default', reasoning: 'high' },
            }),
        ).toEqual({ config_selections: { permission_mode: 'default', reasoning: 'high' } });
    });

    it('notices a selection that was removed', () => {
        expect(harnessProfileChanges(stored, { ...stored, selections: {} }))
            .toEqual({ config_selections: {} });
    });
});

describe('isLocalAgentSignInFailure', () => {
    // The Agent Host's own wording, from `authentication_hint`. Matching it is
    // what puts a Re-check button on the one failure where "try again" cannot
    // work on its own: the harness stays AUTH_REQUIRED and admission keeps
    // refusing until the host re-probes.
    const hint =
        'Claude Code is installed on this computer but not signed in. ' +
        'Sign in to it in a terminal, then press Re-check. ' +
        'Lemma runs it with your credentials and never sees them.';

    it('recognises a signed-out coding agent', () => {
        expect(isLocalAgentSignInFailure(hint)).toBe(true);
    });

    it('leaves every other failure alone', () => {
        // Offering a re-probe here would be a button that fixes nothing.
        expect(isLocalAgentSignInFailure('Agent run was interrupted (timeout or shutdown)')).toBe(false);
        expect(isLocalAgentSignInFailure('No LLM model is configured on this server')).toBe(false);
        expect(isLocalAgentSignInFailure('')).toBe(false);
        expect(isLocalAgentSignInFailure(null)).toBe(false);
        expect(isLocalAgentSignInFailure(undefined)).toBe(false);
    });

    it('is not fooled by an unrelated mention of signing in', () => {
        expect(isLocalAgentSignInFailure('The user is not signed in to Lemma.')).toBe(false);
    });
});
