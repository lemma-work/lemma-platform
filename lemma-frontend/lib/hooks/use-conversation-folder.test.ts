/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { adoptConversationFolder, canBindConversationFolder, folderLabel } from './use-conversation-folder';

vi.mock('@/lib/config', () => ({ isLocalDeployment: () => mockIsLocal() }));

let localDeployment = true;
function mockIsLocal(): boolean {
    return localDeployment;
}

function withShell(invoke: unknown): void {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke } };
}

beforeEach(() => {
    localDeployment = true;
});

afterEach(() => {
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
    vi.restoreAllMocks();
});

describe('choosing a folder', () => {
    it('is offered only where the desktop shell is present', () => {
        expect(canBindConversationFolder()).toBe(false);
        withShell(vi.fn());
        expect(canBindConversationFolder()).toBe(true);
    });

    it('is not offered against a hosted workspace', () => {
        // The commands refuse a non-local install anyway; offering a control
        // that always errors is the part this prevents.
        withShell(vi.fn());
        localDeployment = false;
        expect(canBindConversationFolder()).toBe(false);
    });

    it('adopts a parked choice for the conversation that was just created', async () => {
        const invoke = vi.fn().mockResolvedValue(null);
        withShell(invoke);

        await adoptConversationFolder('conv-1');

        expect(invoke).toHaveBeenCalledWith('adopt_conversation_folder', {
            conversationId: 'conv-1',
        });
    });

    it('lets the conversation run when adoption fails', async () => {
        // Failing a first message over a folder choice would be the worse
        // outcome: the run works, in the ordinary directory.
        withShell(vi.fn().mockRejectedValue(new Error('no')));
        await expect(adoptConversationFolder('conv-1')).resolves.toBeUndefined();
    });

    it('does nothing at all without a shell', async () => {
        await expect(adoptConversationFolder('conv-1')).resolves.toBeUndefined();
    });
});

describe('folderLabel', () => {
    it('names the folder, which is what a chip has room for', () => {
        expect(folderLabel('/Users/me/projects/lemma')).toBe('lemma');
        expect(folderLabel('C:\\Users\\me\\lemma')).toBe('lemma');
        // A trailing separator is still the same folder.
        expect(folderLabel('/Users/me/lemma/')).toBe('lemma');
        // Nothing sensible to shorten: say the whole thing rather than nothing.
        expect(folderLabel('/')).toBe('/');
    });
});
