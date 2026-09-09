// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FunctionRevisionsTab } from './function-revisions-tab';
import { FunctionTestPanel } from './function-test-panel';
import { AppVersionsPanel } from '../app/app-versions-panel';

const state = vi.hoisted(() => ({
    listError: false, codeError: false,
    retry: vi.fn(), retryCode: vi.fn(), promote: vi.fn(), start: vi.fn(),
    setRunId: vi.fn(), refresh: vi.fn(),
    old: { id: 'old', revision_number: 1, revision_hash: 'sha256:abc', is_live: false,
        input_schema: { type: 'object', properties: { historical: { type: 'string' } } } },
    releases: undefined as undefined | Record<string, unknown>[],
}));

const release = (previewUrl: string | null) => ({
    id: 'rel', release_number: 7, version: 'sha256:abc', is_live: false,
    has_source: true, preview_url: previewUrl,
});
vi.mock('@/lib/hooks/use-function-revisions', () => ({
    useFunctionRevisions: () => ({ data: state.listError ? undefined : [state.old], isError: state.listError, isLoading: false, refetch: state.retry }),
    useFunctionRevision: () => ({ data: undefined, isLoading: false, isError: state.codeError, refetch: state.retryCode }),
    usePromoteFunctionRevision: () => ({ mutateAsync: state.promote, isPending: false }),
}));
vi.mock('@/lib/hooks/use-app-releases', () => ({
    useAppReleases: () => ({ data: state.releases, isError: state.listError, isLoading: false, refetch: state.retry }),
    usePromoteAppRelease: () => ({ mutateAsync: state.promote, isPending: false }),
}));
vi.mock('@/lib/hooks/use-functions', () => ({ useFunction: () => ({ data: { name: 'score', input_schema: { properties: { current: { type: 'string' } } } } }) }));
vi.mock('@/lib/hooks/use-infinite-scroll', () => ({ useInfiniteScroll: () => ({ current: null }) }));
vi.mock('@/lib/sdk/lemma-client', () => ({ getLemmaClient: () => ({}) }));
vi.mock('lemma-sdk/react', () => ({
    useFunctionSession: () => ({ start: state.start, setRunId: state.setRunId, runId: null }),
    useFunctionRuns: () => ({ runs: [{ id: 'previous', status: 'COMPLETED', input_data: {} }], refresh: state.refresh }),
}));
vi.mock('@/components/brand/loader', () => ({ StepLoader: () => <span>Loading</span> }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }));

afterEach(cleanup);
beforeEach(() => {
    vi.clearAllMocks(); state.listError = false; state.codeError = false; state.releases = undefined;
    state.start.mockResolvedValue({ id: 'new-run' });
    state.promote.mockResolvedValue({ schema_changed: false });
});

describe('Version history recovery and permissions', () => {
    it('shows a retriable app error instead of claiming there are no versions', async () => {
        state.listError = true;
        render(<AppVersionsPanel podId="pod" appName="app" open onOpenChange={vi.fn()} />);
        expect(screen.queryByText('No versions yet')).toBeNull();
        await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
        expect(state.retry).toHaveBeenCalledOnce();
    });
    it('withholds preview where the stack serves no app host', async () => {
        // `preview_url` is null on Desktop and any single-origin stack. The
        // button used to be offered anyway, and pointed the frame at nothing:
        // it fell back to the live release under a banner announcing v7.
        state.releases = [release(null)];
        render(<AppVersionsPanel podId="pod" appName="app" open onOpenChange={vi.fn()} onPreview={vi.fn()} />);
        expect(screen.getByText('v7')).toBeDefined();
        expect(screen.queryByRole('button', { name: 'Preview' })).toBeNull();
    });
    it('offers preview once a release host exists', async () => {
        const onPreview = vi.fn();
        state.releases = [release('https://orders--r7.apps.example.com')];
        render(<AppVersionsPanel podId="pod" appName="app" open onOpenChange={vi.fn()} onPreview={onPreview} />);
        await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
        expect(onPreview).toHaveBeenCalledOnce();
    });
    it('shows a retriable function list error', async () => {
        state.listError = true;
        render(<FunctionRevisionsTab podId="pod" functionName="score" canUpdate />);
        expect(screen.queryByText('No revisions yet')).toBeNull();
        await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
        expect(state.retry).toHaveBeenCalledOnce();
    });
    it('recovers failed code reads and withholds authoring controls from readers', async () => {
        state.codeError = true;
        render(<FunctionRevisionsTab podId="pod" functionName="score" canUpdate={false} onRunRevision={vi.fn()} />);
        expect(screen.queryByRole('button', { name: 'Run this' })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Set live' })).toBeNull();
        await userEvent.click(screen.getByRole('button', { name: 'View code' }));
        expect(screen.getByRole('alert').textContent).toContain('Could not load');
        await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
        expect(state.retryCode).toHaveBeenCalledOnce();
    });
    it('requires explicit confirmation before promotion', async () => {
        render(<FunctionRevisionsTab podId="pod" functionName="score" canUpdate />);
        await userEvent.click(screen.getByRole('button', { name: 'Set live' }));
        expect(state.promote).not.toHaveBeenCalled();
        await userEvent.click(screen.getAllByRole('button', { name: 'Set live' })[1]);
        expect(state.promote).toHaveBeenCalledWith('r1');
    });
    it('opens the historical schema composer from a previous run and sends the pin', async () => {
        render(<FunctionTestPanel podId="pod" functionId="score" canUpdate initialRunId="previous" />);
        await userEvent.click(screen.getByRole('tab', { name: 'Versions' }));
        await userEvent.click(screen.getByRole('button', { name: 'Run this' }));
        expect(screen.queryByPlaceholderText('Enter current...')).toBeNull();
        await userEvent.type(screen.getByPlaceholderText('Enter historical...'), 'earlier input');
        await userEvent.click(screen.getByRole('button', { name: 'Run Function' }));
        expect(state.start).toHaveBeenCalledWith(expect.objectContaining({ revision: 'r1', input: { historical: 'earlier input' } }));
        await userEvent.click(screen.getByRole('button', { name: 'Use live' }));
        expect(screen.getByPlaceholderText('Enter current...')).toBeDefined();
    });
});
