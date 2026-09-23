// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const status = vi.fn();
vi.mock('@/lib/sdk/lemma-client', () => ({
    getLemmaClient: () => ({ workspace: { status } }),
}));

import { WorkspaceStartingIndicator } from './workspace-starting-indicator';

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
