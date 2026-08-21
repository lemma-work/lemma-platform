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

describe('the first turn is a welcome, not a setup wizard', () => {
    // The regression this exists for: step ONE used to be "put this pod on
    // their phone", and the assistant obeyed — its first act was to run
    // `telegram-setup` and hand back a QR code to someone who had not been told
    // what a pod is. The offer now comes first and waits for an answer.
    it('spends the first turn saying hello and explaining where they are', () => {
        const instructions = firstRun();

        expect(instructions).toContain('Your first turn is a welcome and one offer');
        expect(instructions).toContain('what this place actually is');
    });

    it('bans setup in that turn, which is the whole point of the rewrite', () => {
        const instructions = firstRun();

        expect(instructions).toContain(
            'Do not run a command, create a resource, or show a QR code in this turn',
        );
        expect(instructions).toContain('You are asking, not setting up');
    });

    it('names what a pod is for, rather than assuming they know', () => {
        const instructions = firstRun();

        expect(instructions).toContain('Nothing here is obvious to them');
        expect(instructions).toContain('Telegram, Slack, email');
        expect(instructions).toContain('schedule');
    });

    it('makes Telegram a question and stops there', () => {
        const instructions = firstRun();
        const offer = instructions.indexOf('make exactly one offer and stop');
        const setup = instructions.indexOf('If they say yes to Telegram, set it up now');

        expect(offer).toBeGreaterThan(-1);
        expect(setup).toBeGreaterThan(offer);
    });

    it('takes no for an answer, so the offer stays an offer', () => {
        const instructions = firstRun();

        expect(instructions).toContain('Telegram is an offer, not a step');
        expect(instructions).toContain('never ask a second time in a row');
    });

    it('keeps the reason messaging the bot once matters', () => {
        // The handshake: a chat bot cannot open a conversation, so the pod can
        // only reach out after it has been reached.
        expect(firstRun()).toContain('message *them*');
    });

    it('bans a corporate greeting outright, since that is what it drifts to', () => {
        expect(firstRun()).toContain("I'm excited to help you get started");
    });
});

describe('the arc after the first question', () => {
    it('runs Telegram, then the build, then the invite', () => {
        const instructions = firstRun();
        const telegram = instructions.indexOf('If they say yes to Telegram');
        const build = instructions.indexOf('Build them something real');
        const share = instructions.indexOf('Then make it theirs together');

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

    it('binds the bot to the pod assistant, so no agent has to exist first', () => {
        expect(firstRun()).toContain('Pass no `--agent`');
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
        expect(firstRun()).toContain('belongs in a widget rather than a paragraph');
    });

    it('exempts the greeting from that, so hello is not a widget', () => {
        expect(firstRun()).toContain('Use widgets generously — but not to say hello');
    });
});

describe('a later pod gets the same conversation, minus the explanation', () => {
    // The regression this exists for: the later-pod branch was three terse
    // lines ending in "start building", so a second pod got no pacing, no
    // widgets, no Telegram — just tables nobody asked for.
    it('skips the product introduction but keeps everything else', () => {
        const instructions = laterPod();

        expect(instructions).not.toContain('first conversation this person has ever had');
        expect(instructions).toContain('already use Lemma');
        expect(instructions).toContain('keep the welcome to one short line');
    });

    it.each([
        ['the pacing rule', 'Never stack two things in a turn'],
        ['propose-before-build', 'Propose before you build'],
        ['the Telegram offer', 'make exactly one offer and stop'],
        ['the Telegram step', 'lemma surfaces telegram-setup'],
        ['the widget guidance', 'belongs in a widget rather than a paragraph'],
        ['the invite step', 'Then make it theirs together'],
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

describe('a stated intent replaces both the greeting and the welcome turn', () => {
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

    it('drops the welcome, because they asked a question and want an answer', () => {
        // Welcoming someone who just typed a brief, then asking them about
        // Telegram, is answering a question nobody asked.
        const { instructions } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'Telegram Pod',
                isFirstPod: false,
                openingMessage: 'Build a Telegram agent that ',
            }),
        );

        expect(instructions).toContain('that message is the brief');
        expect(instructions).not.toContain('Your first turn is a welcome and one offer');
    });

    it('still paces itself and still gets to the phone eventually', () => {
        const instructions = buildNewPodInstructions({
            podName: 'Telegram Pod',
            isFirstPod: false,
            statedIntent: true,
        });

        expect(instructions).toContain('Never stack two things in a turn');
        expect(instructions).toContain('Propose before you build');
        expect(instructions).toContain('putting this pod on their phone later');
    });

    it('keeps the new-pod framing underneath the start path brief', () => {
        const { instructions } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'Telegram Pod',
                isFirstPod: false,
                openingMessage: 'Build a Telegram agent that ',
                extraInstructions: 'Wire the Telegram surface.',
            }),
        );

        expect(instructions?.toLowerCase()).toContain('do not create another pod');
        expect(instructions).toContain('Wire the Telegram surface.');
    });

    it('falls back to the greeting and the welcome when nothing was stated', () => {
        const { message, instructions } = launchFrom(
            buildNewPodConversationHref({
                podId: 'pod-1',
                podName: 'X Pod',
                isFirstPod: false,
                openingMessage: '   ',
            }),
        );

        expect(message).toBe(NEW_POD_OPENING_MESSAGE);
        expect(instructions).toContain('Your first turn is a welcome and one offer');
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
