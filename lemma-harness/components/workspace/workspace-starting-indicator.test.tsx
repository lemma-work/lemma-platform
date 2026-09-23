// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const status = vi.fn();
vi.mock('@/lib/sdk/lemma-client', () => ({
    getLemmaClient: () => ({ workspace: { status } }),
}));

import { downloadFraction, WorkspaceStartingIndicator } from './workspace-starting-indicator';

function renderIndicator() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <WorkspaceStartingIndicator />
        </QueryClientProvider>,
    );
}

describe('WorkspaceStartingIndicator', () => {
    beforeEach(() => status.mockReset());
    afterEach(() => cleanup());

    it('says the workspace is downloading, with the reason, while it is', async () => {
        status.mockResolvedValue({
            state: 'downloading',
            detail: 'Downloading the workspace image. The first start after an update takes a few minutes.',
        });
        renderIndicator();
        expect(await screen.findByText('Downloading your workspace')).toBeTruthy();
        expect(screen.getByText(/first start after an update/)).toBeTruthy();
        expect(screen.getByRole('status')).toBeTruthy();
    });

    it('says the computer is starting', async () => {
        status.mockResolvedValue({ state: 'starting', detail: 'Starting your computer.' });
        renderIndicator();
        expect(await screen.findByText('Starting your computer')).toBeTruthy();
    });

    it.each(['ready', 'asleep', 'unavailable'] as const)('shows nothing when %s', async (state) => {
        status.mockResolvedValue({ state, detail: null });
        renderIndicator();
        await waitFor(() => expect(status).toHaveBeenCalled());
        expect(screen.queryByRole('status')).toBeNull();
    });
});

describe('downloadFraction', () => {
    it('is measured only while downloading with a known total', () => {
        expect(downloadFraction({ state: 'downloading', done_mb: 245, total_mb: 980 } as never)).toBe(0.25);
        expect(downloadFraction({ state: 'downloading' } as never)).toBeNull();
        expect(downloadFraction({ state: 'starting', done_mb: 1, total_mb: 2 } as never)).toBeNull();
        expect(downloadFraction({ state: 'downloading', done_mb: 5, total_mb: 0 } as never)).toBeNull();
    });
});
