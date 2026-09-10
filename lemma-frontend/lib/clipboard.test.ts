/**
 * The global test environment is `node`, and this is a DOM fallback: the whole
 * point of `execCommand` here is that it runs where the async clipboard API
 * does not exist, which needs a `document` to select in.
 *
 * @vitest-environment jsdom
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { copyText } from './clipboard';

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

function setClipboard(value: unknown): void {
    Object.defineProperty(navigator, 'clipboard', {
        value,
        configurable: true,
        writable: true,
    });
}

afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
    else setClipboard(undefined);
    vi.restoreAllMocks();
});

describe('copyText', () => {
    it('uses the async clipboard when the context allows one', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        setClipboard({ writeText });

        await expect(copyText('hello')).resolves.toBeUndefined();
        expect(writeText).toHaveBeenCalledWith('hello');
    });

    it('still copies when navigator.clipboard does not exist at all', async () => {
        // The desktop workspace is served from an `http://*.localhost` origin
        // that WKWebView does not treat as a secure context, so the property is
        // absent. Reading `.writeText` off it threw a TypeError, and the copy
        // button's empty catch turned that into a button that did nothing.
        setClipboard(undefined);
        const execCommand = vi.fn().mockReturnValue(true);
        document.execCommand = execCommand as unknown as typeof document.execCommand;

        await expect(copyText('hello')).resolves.toBeUndefined();
        expect(execCommand).toHaveBeenCalledWith('copy');
    });

    it('falls back when the async clipboard rejects', async () => {
        setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('denied')) });
        const execCommand = vi.fn().mockReturnValue(true);
        document.execCommand = execCommand as unknown as typeof document.execCommand;

        await expect(copyText('hello')).resolves.toBeUndefined();
        expect(execCommand).toHaveBeenCalledWith('copy');
    });

    it('throws when neither path worked, rather than reporting success', async () => {
        setClipboard(undefined);
        document.execCommand = vi.fn().mockReturnValue(false) as unknown as typeof document.execCommand;

        await expect(copyText('hello')).rejects.toThrow(/not available/);
    });

    it('leaves no scratch element behind', async () => {
        setClipboard(undefined);
        document.execCommand = vi.fn().mockReturnValue(true) as unknown as typeof document.execCommand;

        await copyText('hello');

        expect(document.querySelectorAll('textarea')).toHaveLength(0);
    });
});
