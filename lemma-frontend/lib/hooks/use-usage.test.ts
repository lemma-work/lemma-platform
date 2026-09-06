// @vitest-environment jsdom
import { createElement, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { MyUsageLimitsResponse } from 'lemma-sdk';
import { useMyUsageLimits } from './use-usage';

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@/lib/sdk/lemma-client', () => ({ getLemmaClient: () => ({ request }) }));
afterEach(() => { cleanup(); request.mockReset(); });

function result(organizationId: string): MyUsageLimitsResponse {
    return { organization_id: organizationId, payer: 'organization', plan_name: organizationId, windows: [], allowed: true, warning_percent: 80 };
}

function harness() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    return function UsageQueryProvider({ children }: { children: ReactNode }) {
        return createElement(QueryClientProvider, { client }, children);
    };
}

it('does not fetch while closed, and refetches when opened again', async () => {
    request.mockResolvedValue(result('org-a'));
    const hook = renderHook(({ enabled }) => useMyUsageLimits('org-a', { enabled }), { initialProps: { enabled: false }, wrapper: harness() });
    expect(request).not.toHaveBeenCalled();
    hook.rerender({ enabled: true });
    await waitFor(() => expect(hook.result.current.data?.plan_name).toBe('org-a'));
    hook.rerender({ enabled: false });
    hook.rerender({ enabled: true });
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
});

it('cannot display a previous organization response after switching context', async () => {
    const pending = Promise.withResolvers<MyUsageLimitsResponse>();
    request.mockImplementation((_method: string, _path: string, options: { params: { organization_id: string } }) => options.params.organization_id === 'org-a' ? pending.promise : Promise.resolve(result('org-b')));
    const hook = renderHook(({ id }) => useMyUsageLimits(id), { initialProps: { id: 'org-a' }, wrapper: harness() });
    hook.rerender({ id: 'org-b' });
    expect(hook.result.current.data).toBeUndefined();
    await waitFor(() => expect(hook.result.current.data?.organization_id).toBe('org-b'));
    await act(async () => { pending.resolve(result('org-a')); await pending.promise; });
    expect(hook.result.current.data?.organization_id).toBe('org-b');
});
