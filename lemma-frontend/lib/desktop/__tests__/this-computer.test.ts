import { afterEach, describe, expect, it } from 'vitest';

import { ThisComputer, thisComputer } from '@/lib/desktop/this-computer';
import { discoveryHeadline, discoveryLines } from '@/components/agents/harness-discovery-rows';

const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

function pretendToBe(agent: { platform?: string; userAgent?: string } | undefined) {
    if (agent === undefined) {
        // `delete` rather than assigning undefined: the helper branches on
        // `typeof navigator === "undefined"`, which is what server rendering
        // actually looks like.
        delete (globalThis as { navigator?: unknown }).navigator;
        return;
    }
    Object.defineProperty(globalThis, 'navigator', {
        value: agent,
        configurable: true,
        writable: true,
    });
}

afterEach(() => {
    if (original) Object.defineProperty(globalThis, 'navigator', original);
    else delete (globalThis as { navigator?: unknown }).navigator;
});

describe('what to call the machine Lemma is installed on', () => {
    it('says Mac on a Mac', () => {
        pretendToBe({ platform: 'MacIntel', userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)' });
        expect(thisComputer()).toBe('this Mac');
    });

    it('says PC on Windows, which is the whole point', () => {
        // The desktop app has a Windows build and a `desktop-windows` CI job,
        // and every one of these sentences used to name hardware that build's
        // users do not have.
        pretendToBe({ platform: 'Win32', userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' });
        expect(thisComputer()).toBe('this PC');
    });

    it('reads the modern platform hint when the deprecated one is empty', () => {
        // Chromium is progressively freezing `navigator.platform`. WKWebView
        // still reports it, which is why both are consulted.
        pretendToBe({ userAgent: 'Mozilla/5.0' } as never);
        Object.defineProperty(globalThis.navigator, 'userAgentData', {
            value: { platform: 'Windows' },
            configurable: true,
        });
        expect(thisComputer()).toBe('this PC');
    });

    it('is neutral rather than wrong where the machine is unknowable', () => {
        // Server rendering. The desktop shell renders these views on the local
        // frontend service first, and a guess there would ship the wrong noun
        // into the HTML for half the users.
        pretendToBe(undefined);
        expect(thisComputer()).toBe('this computer');

        pretendToBe({ platform: 'Linux x86_64', userAgent: 'Mozilla/5.0 (X11; Linux x86_64)' });
        expect(thisComputer()).toBe('this computer');
    });

    it('capitalises without shouting', () => {
        pretendToBe({ platform: 'Win32', userAgent: 'Windows NT' });
        expect(ThisComputer()).toBe('This PC');
    });
});

describe('the copy that reaches the screen', () => {
    it('names the right machine in the empty state a Windows user actually sees', () => {
        pretendToBe({ platform: 'Win32', userAgent: 'Mozilla/5.0 (Windows NT 10.0)' });
        expect(discoveryHeadline('settled', 0)).toBe('No coding agents found on this PC');
        expect(discoveryHeadline('scanning', 0)).toBe('Looking for coding agents on this PC');
        expect(discoveryHeadline('settled', 3)).toBe('Found 3 coding agents on this PC');
    });

    it('does not tell a Windows user about a macOS file-access prompt', () => {
        pretendToBe({ platform: 'Win32', userAgent: 'Mozilla/5.0 (Windows NT 10.0)' });
        expect(discoveryLines('scanning', 0).join(' ')).not.toContain('macOS');

        pretendToBe({ platform: 'MacIntel', userAgent: 'Macintosh; Intel Mac OS X' });
        expect(discoveryLines('scanning', 0).join(' ')).toContain('macOS');
    });
});
