// @vitest-environment jsdom
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';

const list = vi.fn();
const remove = vi.fn();

vi.mock('@/lib/sdk/lemma-client', () => ({
    getLemmaClient: () => ({ webLogins: { list, remove } }),
}));

const { useRemoveWebLogin, useWebLogins, webLoginsQueryKey } =
    await import('@/lib/hooks/use-web-logins');

/**
 * Reading a browser, not a table -- and the two places that distinction bites.
 *
 * `wake` is part of the cache key because the two answers are different
 * facts: a paused computer says `sleeping` rather than being started, so a
 * woken read must not be served from a sleeping one or the list looks empty
 * for a sandbox that is signed in to things. And forgetting is done from the
 * woken list while the page renders the sleeping one, so an invalidation
 * that named only the key it was called under left the next visit showing a
 * site that had just been signed out of.
 */

const wrapper = (client: QueryClient) =>
    function Wrapper({ children }: { children: ReactNode }) {
        return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    };

const freshClient = () =>
    new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

afterEach(() => {
    cleanup();
    list.mockReset();
    remove.mockReset();
});

describe('webLoginsQueryKey', () => {
    it('separates a woken read from a sleeping one', () => {
        expect(webLoginsQueryKey(true)).not.toEqual(webLoginsQueryKey(false));
    });

    it('sits under one prefix, so both can be invalidated together', () => {
        expect(webLoginsQueryKey(true)[0]).toBe('web-logins');
        expect(webLoginsQueryKey(false)[0]).toBe('web-logins');
    });
});

describe('useWebLogins', () => {
    it('asks nothing of the sandbox until the card is opened', async () => {
        // The card sits on a page of eighty connectors. Asking a sandbox
        // anything to render a door nobody has opened is a round trip spent
        // on nothing -- and on a paused computer it is a round trip that
        // could start one.
        renderHook(() => useWebLogins(false, false), {
            wrapper: wrapper(freshClient()),
        });

        await new Promise((resolve) => setTimeout(resolve, 10));
        expect(list).not.toHaveBeenCalled();
    });

    it('does not wake a paused computer to answer', async () => {
        list.mockResolvedValue({ items: [], sleeping: true });

        const { result } = renderHook(() => useWebLogins(), {
            wrapper: wrapper(freshClient()),
        });

        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(list).toHaveBeenCalledWith({ wake: false });
        expect(result.current.data?.sleeping).toBe(true);
    });

    it('passes the wake through when somebody asks for it', async () => {
        list.mockResolvedValue({
            items: [{ origin: 'https://example.com' }],
            sleeping: false,
        });

        const { result } = renderHook(() => useWebLogins(true), {
            wrapper: wrapper(freshClient()),
        });

        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(list).toHaveBeenCalledWith({ wake: true });
    });
});

describe('useRemoveWebLogin', () => {
    it('invalidates the sleeping read as well as the woken one', async () => {
        // The regression this pins: forgetting happens on the woken list,
        // and the next visit renders the sleeping one. Invalidating only the
        // key the mutation ran under left a signed-out site on screen.
        const client = freshClient();
        client.setQueryData(webLoginsQueryKey(true), {
            items: [],
            sleeping: false,
        });
        client.setQueryData(webLoginsQueryKey(false), {
            items: [],
            sleeping: true,
        });
        remove.mockResolvedValue(undefined);
        const invalidated = vi.spyOn(client, 'invalidateQueries');

        const { result } = renderHook(() => useRemoveWebLogin(), {
            wrapper: wrapper(client),
        });
        await act(async () => {
            await result.current.mutateAsync('https://example.com');
        });

        expect(remove).toHaveBeenCalledWith('https://example.com');
        expect(invalidated).toHaveBeenCalledWith({ queryKey: ['web-logins'] });
    });

    it('leaves the cache alone when the sign-out failed', async () => {
        // A refused clear now comes back as a 502 rather than a cheerful
        // zero, and an invalidation on that path would refetch a list that
        // still holds the site and look like the removal simply did not
        // take.
        const client = freshClient();
        remove.mockRejectedValue(
            new Error('the browser refused to clear 2 of 2 origins'),
        );
        const invalidated = vi.spyOn(client, 'invalidateQueries');

        const { result } = renderHook(() => useRemoveWebLogin(), {
            wrapper: wrapper(client),
        });
        await act(async () => {
            await expect(
                result.current.mutateAsync('https://example.com'),
            ).rejects.toThrow();
        });

        expect(invalidated).not.toHaveBeenCalled();
    });
});
