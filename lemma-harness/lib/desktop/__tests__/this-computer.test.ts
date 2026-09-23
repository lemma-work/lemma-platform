import { afterEach, describe, expect, it } from 'vitest';

import { NEUTRAL_FOR_TESTS, ThisComputer, thisComputer } from '@/lib/desktop/this-computer';
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
    it('renders whichever machine it is handed, and reads no globals of its own', () => {
        // The noun is a parameter now. It used to be read inside these
        // functions, which answered "this computer" on the server and "this
        // Mac" on the first client render -- a hydration mismatch on every
        // string here. The component holds `useThisComputer()`, which is the
        // same answer in both renders.
        //
        // Pinned by making `navigator` disagree with the argument: if these
        // functions still consulted it, these assertions would fail.
        pretendToBe({ platform: 'MacIntel', userAgent: 'Macintosh; Intel Mac OS X' });
        expect(discoveryHeadline('settled', 0, 'this PC')).toBe(
            'No coding agents found on this PC',
        );
        expect(discoveryHeadline('scanning', 0, 'this PC')).toBe(
            'Looking for coding agents on this PC',
        );
        expect(discoveryHeadline('settled', 3, 'this PC')).toBe(
            'Found 3 coding agents on this PC',
        );
        expect(discoveryLines('unavailable', 0, 'this PC').join(' ')).toContain('this PC');
    });

    it('keeps its line count the same on every platform', () => {
        // Not a style point. A different array length between the server render
        // and the first client one is a *structural* hydration mismatch, and
        // React repairs those by discarding the server subtree rather than by
        // patching the text. The line about file access is phrased to be true
        // everywhere instead of being dropped off macOS.
        const onWindows = discoveryLines('scanning', 0, 'this PC');
        const onMac = discoveryLines('scanning', 0, 'this Mac');
        const onTheServer = discoveryLines('scanning', 0, 'this computer');

        expect(onWindows).toHaveLength(onMac.length);
        expect(onTheServer).toHaveLength(onMac.length);
        expect(onWindows.join(' ')).not.toContain('macOS');
    });

    it('renders the same on the server and the first client pass', () => {
        // What `useThisComputer` is for: `thisComputer()` alone answers
        // differently in the two, which is a mismatch on every caller.
        pretendToBe(undefined);
        const server = thisComputer();
        expect(server).toBe('this computer');
        // The hook's server snapshot is the same value, so the two renders
        // agree and the specific noun arrives in the commit afterwards.
        expect(NEUTRAL_FOR_TESTS).toBe(server);
    });
});
