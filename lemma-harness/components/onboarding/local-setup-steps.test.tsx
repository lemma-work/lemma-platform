// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const connection = vi.hoisted(() => ({
    status: null,
    error: null as string | null,
    connectError: null as string | null,
    retryConnect: vi.fn(),
    refetch: vi.fn().mockResolvedValue(true),
}));
vi.mock('@/lib/desktop/auto-connect', () => ({ useAutoConnectThisComputer: () => connection }));
vi.mock('@/lib/desktop/this-computer', () => ({ useThisComputer: () => 'this Mac' }));
vi.mock('@/lib/desktop/local-capabilities', () => ({
    useDesktopBridge: () => true,
    useLocalAiStatus: () => ({ status: 'needs_setup' }),
    openLocalSettings: vi.fn(),
}));
vi.mock('@/lib/sdk/lemma-client', () => ({ getLemmaApiBaseUrl: () => 'http://localhost:8710' }));
vi.mock('@/lib/hooks/use-agent-runtime', () => ({
    useAgentHostHarnesses: () => ({ data: { items: [] }, isLoading: false }),
    useAgentHosts: () => ({ data: { items: [] } }),
    useManagedAgentRuntimes: () => ({ data: { items: [] } }),
    useRestoreAgentRuntime: () => ({ mutateAsync: vi.fn() }),
}));
import { LocalIntelligenceStep } from './local-setup-steps';

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
    connection.error = null;
    connection.connectError = null;
    vi.clearAllMocks();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
});
afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
});
async function render() {
    await act(async () => root.render(<LocalIntelligenceStep organizationId={null} steps={[]} onContinue={() => {}} />));
}

describe('local agent onboarding recovery', () => {
    it('uses the provider column for the selected form and can return to the choices', async () => {
        await render();
        const button = (label: string) => [...container.querySelectorAll('button')].find((item) => item.textContent?.startsWith(label));
        expect(button('Ollama')).toBeDefined();
        await act(async () => button('OpenAI')!.click());
        expect(container.querySelector('input[aria-label="OpenAI API key"]')).not.toBeNull();
        expect(button('Ollama')).toBeUndefined();
        expect(button('Continue')).toBeDefined();
        await act(async () => button('Choose another provider')!.click());
        expect(container.querySelector('input[type="password"]')).toBeNull();
        expect(button('Ollama')).toBeDefined();
    });
    it.each(['error', 'connectError'] as const)('shows a %s failure and retries from the setup screen', async (field) => {
        connection[field] = 'The local agent host could not connect';
        await render();
        expect(container.querySelector('[role="alert"]')?.textContent).toContain(connection[field]);
        expect(container.textContent).not.toContain('Starting the agent host');
        const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry connection');
        expect(retry).toBeDefined();
        await act(async () => retry!.click());
        expect(connection.retryConnect).toHaveBeenCalledTimes(1);
        expect(connection.refetch).toHaveBeenCalledTimes(1);
        connection[field] = null;
        await render();
        expect(container.querySelector('[role="alert"]')).toBeNull();
        expect(container.textContent).toContain('Starting the agent host');
        const actions = container.querySelector('footer[aria-label="Setup actions"]');
        expect(actions?.textContent).toContain('Continue');
        expect(actions?.closest('[data-testid="setup-content"]')).toBeNull();
    });
});
