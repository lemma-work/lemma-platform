import { describe, expect, it } from 'vitest';

import {
    NEW_POD_OPENING_MESSAGE,
    buildNewPodInstructions,
    buildNewPodConversationHref,
} from './new-pod-conversation';

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

const firstRun = (podName = 'Ada Pod', workDomain?: string) =>
    buildNewPodInstructions({ podName, workDomain, isFirstPod: true });

const laterPod = (podName = 'GTM Pod') =>
    buildNewPodInstructions({ podName, isFirstPod: false });

describe('every new pod', () => {
    it('forbids creating a second pod, which is what an agent does otherwise', () => {
        // Both variants say it; only the first-run script says it mid-sentence.
        for (const instructions of [firstRun(), laterPod()]) {
            expect(instructions?.toLowerCase()).toContain('do not create another pod');
        }
    });

    it('opens a conversation rather than pod home, which is a launcher', () => {
        const { path, message, metadata } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'GTM Pod',
                isFirstPod: false,
            }),
        );

        expect(path).toBe('/pod/pod-1/conversations/new');
        expect(message).toBe(NEW_POD_OPENING_MESSAGE);
        expect(metadata).toMatchObject({ source: 'create_pod', first_run: false });
    });

    it('keeps the opening message trivial, because instructions carry the work', () => {
        expect(NEW_POD_OPENING_MESSAGE.length).toBeLessThan(12);
    });

    it('treats a typed name as the clearest statement of intent available', () => {
        expect(laterPod('GTM Pod')).toContain('They named it "GTM Pod"');
    });

    it('reads nothing into a name the user never chose', () => {
        expect(laterPod('Untitled pod')).not.toContain('They named it');
        // A first pod is named for the person, never by them: "Kapeed Pod" is
        // an identity, not a brief.
        expect(firstRun('Kapeed Pod')).not.toContain('They named it');
        expect(firstRun('Personal Pod')).not.toContain('They named it');
    });

    it('escapes a pod id that would otherwise break the URL', () => {
        const { path } = launchFrom(
            buildNewPodConversationHref({
                podId: 'a/b c',
                podName: 'X Pod',
                isFirstPod: false,
            }),
        );

        expect(path).toBe('/pod/a%2Fb%20c/conversations/new');
    });
});

describe('the first ten minutes are paced, in order', () => {
    it('runs Telegram, then the app, then the invite', () => {
        const instructions = firstRun();
        const telegram = instructions.indexOf('ONE — put this pod on their phone');
        const build = instructions.indexOf('TWO — build them something real');
        const share = instructions.indexOf('THREE — make it theirs together');

        expect(telegram).toBeGreaterThan(-1);
        expect(build).toBeGreaterThan(telegram);
        expect(share).toBeGreaterThan(build);
    });

    it('names the commands and skills the Telegram step actually needs', () => {
        const instructions = firstRun();

        expect(instructions).toContain('lemma surfaces telegram-setup');
        expect(instructions).toContain('telegram-setup-status');
        expect(instructions).toContain('lemma-builder');
        expect(instructions).toContain('lemma-widget');
    });

    it('binds the bot to Lem, so no agent has to exist first', () => {
        expect(firstRun()).toContain('Pass no `--agent`');
    });

    it('says why messaging the bot once matters, not just that it exists', () => {
        // The handshake: a chat bot cannot open a conversation, so the pod can
        // only reach out after it has been reached.
        expect(firstRun()).toContain('message *them*');
    });

    it('does not send it inspecting an empty pod', () => {
        const instructions = firstRun();

        expect(instructions).toContain('The pod is empty');
        expect(instructions).toContain('Do not inspect it');
        expect(instructions).not.toContain('inspect what is already there first');
    });

    it('gates the invite on something existing worth sharing', () => {
        expect(firstRun()).toContain('worth showing, and not before');
    });

    it('stops after the QR instead of stacking the next question onto it', () => {
        // The failure this exists for: one turn carrying the QR, the research
        // and four app options at once, which reads as a wall, not a
        // conversation.
        const instructions = firstRun();

        expect(instructions).toContain('Never stack two things in a turn');
        expect(instructions).toContain('End your turn with nothing else in it');
        expect(instructions).toContain('do not report it yet');
    });

    it('points the background research at their work domain', () => {
        expect(firstRun('Ada Pod', 'linkedin.com')).toContain('linkedin.com');
        expect(firstRun('Ada Pod')).not.toContain('start the background research there');
    });

    it('asks for widgets over prose, since that is the demonstration', () => {
        expect(firstRun()).toContain('prefer a widget over a paragraph');
    });
});

describe('a later pod gets the same conversation, minus the welcome', () => {
    // The regression this exists for: the later-pod branch was three terse
    // lines ending in "start building", so a second pod got no pacing, no
    // widgets, no Telegram — just tables nobody asked for.
    it('skips the product introduction but keeps everything else', () => {
        const instructions = laterPod();

        expect(instructions).not.toContain('first conversation this person has ever had');
        expect(instructions).toContain('already use Lemma');
    });

    it.each([
        ['the pacing rule', 'Never stack two things in a turn'],
        ['propose-before-build', 'Propose before you build'],
        ['the Telegram step', 'lemma surfaces telegram-setup'],
        ['the widget guidance', 'prefer a widget over a paragraph'],
        ['the invite step', 'THREE — make it theirs together'],
    ])('still carries %s', (_label, phrase) => {
        expect(laterPod()).toContain(phrase);
    });

    it('never tells it to start building unasked', () => {
        for (const instructions of [firstRun(), laterPod()]) {
            expect(instructions).not.toContain('and start building');
            expect(instructions).toContain('wait for them to agree');
        }
    });
});

describe('a stated intent replaces the placeholder greeting', () => {
    it('sends what the user picked instead of "Hi"', () => {
        const { message } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'Telegram Pod',
                isFirstPod: false,
                openingMessage: 'Build a Telegram agent that ',
            }),
        );

        expect(message).toBe('Build a Telegram agent that');
    });

    it('keeps the new-pod framing underneath the start path brief', () => {
        const { instructions } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'Telegram Pod',
                isFirstPod: false,
                extraInstructions: 'Wire the Telegram surface.',
            }),
        );

        expect(instructions?.toLowerCase()).toContain('do not create another pod');
        expect(instructions).toContain('Wire the Telegram surface.');
    });

    it('falls back to the greeting when nothing was stated', () => {
        const { message } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'X Pod',
                isFirstPod: false,
                openingMessage: '   ',
            }),
        );

        expect(message).toBe(NEW_POD_OPENING_MESSAGE);
    });

    it('lets a caller add origin metadata without losing the defaults', () => {
        const { metadata } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'X Pod',
                isFirstPod: false,
                metadata: { start_path: 'telegram' },
            }),
        );

        expect(metadata).toMatchObject({ pod_id: 'pod-1', start_path: 'telegram' });
    });
});
