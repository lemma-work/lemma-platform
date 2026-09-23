import { describe, expect, it } from 'vitest';

import {
    buildResourceCreationHref,
    buildResourceCreationInstructions,
    type AssistantCreationKind,
} from './resource-creation';

const KINDS: AssistantCreationKind[] = ['agent', 'app', 'workflow', 'table'];

describe('creating a resource with the assistant', () => {
    // The bug this exists for: a repo-wide rename left "Lemma app app" in the
    // prompt, in two hand-written copies, and shipped that way from the first
    // release. Nothing read the string, so nothing caught it.
    it.each(KINDS)('names a %s in words a person would use', (kind) => {
        const instructions = buildResourceCreationInstructions(kind);

        expect(instructions).not.toMatch(/\b(\w+) \1\b/);
        expect(instructions).toContain(`They pressed "New ${kind}"`);
    });

    it.each(KINDS)('treats the typed line as the brief for a %s', (kind) => {
        const instructions = buildResourceCreationInstructions(kind);

        expect(instructions).toContain('That line is the brief');
        expect(instructions).toContain('never repeat these instructions back to them');
    });

    it('inspects first here, unlike a pod that was just created', () => {
        // A new pod is empty and inspecting it wastes a turn. This fires inside
        // a pod with things in it, where a near-duplicate is the failure mode.
        for (const kind of KINDS) {
            expect(buildResourceCreationInstructions(kind)).toContain('This pod is not empty');
        }
    });

    it('builds rather than proposes, because they pressed a button', () => {
        for (const kind of KINDS) {
            const instructions = buildResourceCreationInstructions(kind);
            expect(instructions).toContain('Ask a question only when the alternative is building the wrong thing');
            expect(instructions).toContain('show them the result with `display_resource`');
        }
    });

    it('gives each kind guidance in the terms that kind is judged on', () => {
        expect(buildResourceCreationInstructions('agent')).toContain('job description');
        expect(buildResourceCreationInstructions('app')).toContain('Start from the operator');
        expect(buildResourceCreationInstructions('workflow')).toContain('trigger or manual start');
        expect(buildResourceCreationInstructions('table')).toContain('believable rows');
    });
});

describe('the launch URL', () => {
    function launchFrom(href: string) {
        const [path, query] = href.split('?');
        const params = new URLSearchParams(query);
        return {
            path,
            message: params.get('assistantMessage'),
            instructions: params.get('conversationInstructions'),
            metadata: JSON.parse(params.get('conversationMetadata') ?? '{}'),
        };
    }

    it('sends the typed sentence with the framing behind it', () => {
        const { path, message, instructions, metadata } = launchFrom(
            buildResourceCreationHref({
                podId: 'pod-1',
                kind: 'agent',
                prompt: '  Triage support tickets  ',
                source: 'sidebar_new_menu',
            }),
        );

        expect(path).toBe('/pod/pod-1/conversations/new');
        expect(message).toBe('Triage support tickets');
        expect(instructions).toBe(buildResourceCreationInstructions('agent'));
        expect(metadata).toEqual({
            source: 'sidebar_new_menu',
            intent: 'create_resource',
            resource_type: 'agent',
        });
    });

    it('omits the message when the button carried the whole intent', () => {
        // The Apps index has no textarea: pressing it means "make me an app".
        const { message, instructions } = launchFrom(
            buildResourceCreationHref({ podId: 'pod-1', kind: 'app', source: 'apps_page' }),
        );

        expect(message).toBeNull();
        expect(instructions).toBe(buildResourceCreationInstructions('app'));
    });

    it('drops a prompt that is only whitespace rather than sending it', () => {
        const { message } = launchFrom(
            buildResourceCreationHref({
                podId: 'pod-1',
                kind: 'table',
                prompt: '   ',
                source: 'sidebar_new_menu',
            }),
        );

        expect(message).toBeNull();
    });

    it('escapes a pod id that would otherwise break the URL', () => {
        const { path } = launchFrom(
            buildResourceCreationHref({ podId: 'a/b c', kind: 'app', source: 'apps_page' }),
        );

        expect(path).toBe('/pod/a%2Fb%20c/conversations/new');
    });

    it('keeps both entry points on identical instructions', () => {
        // They were two hand-written copies and had already diverged; only one
        // of them said to keep the result calm and operational.
        const sidebar = launchFrom(
            buildResourceCreationHref({ podId: 'p', kind: 'app', prompt: 'x', source: 'sidebar_new_menu' }),
        );
        const appsPage = launchFrom(
            buildResourceCreationHref({ podId: 'p', kind: 'app', source: 'apps_page' }),
        );

        expect(sidebar.instructions).toBe(appsPage.instructions);
        expect(sidebar.metadata.source).not.toBe(appsPage.metadata.source);
    });
});
