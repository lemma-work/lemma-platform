'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Cpu, RefreshCw, TerminalSquare } from '@/components/ui/icons';
import {
    agentHostBridge,
    useThisComputer,
    type AgentHostTarget,
} from '@/lib/desktop/agent-host-bridge';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';
import { useCreateAgentHostPairing } from '@/lib/hooks/use-agent-runtime';
import { allowAutoConnect, declineAutoConnect } from '@/lib/desktop/auto-connect';
import { cn } from '@/lib/utils';
import { describeThisComputer, selectWorkspaceTarget, type Tone } from './this-computer-status';

export type ThisComputerState = {
    /** The paired-computer id the backend knows this machine by, if any. */
    hostId: string | null;
};

function StatusDot({ tone }: { tone: Tone }) {
    return (
        <span
            className={cn(
                'size-2 shrink-0 rounded-full',
                tone === 'ok' && 'bg-[var(--state-success)]',
                tone === 'warn' && 'bg-[var(--state-warning)]',
                tone === 'muted' && 'bg-[var(--text-tertiary)]',
            )}
            aria-hidden="true"
        />
    );
}

function formatUptime(seconds: number | null): string | null {
    if (!seconds || seconds < 60) return null;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return hours > 0 ? `up ${hours}h ${minutes}m` : `up ${minutes}m`;
}

export function ThisComputerCard({
    onHostIdChange,
    onPaired,
}: {
    onHostIdChange?: (hostId: string | null) => void;
    onPaired?: () => void;
}) {
    const { isDesktop, status, error, refetch } = useThisComputer();
    const createPairing = useCreateAgentHostPairing();
    const [displayName, setDisplayName] = useState('This computer');
    const [busy, setBusy] = useState<string | null>(null);
    const [confirmDisconnect, setConfirmDisconnect] = useState(false);

    // This workspace's pairing, not whichever one happens to be first: a Mac
    // paired to its own local stack and then opened against a hosted workspace
    // has two, and only one of them is what this card is about.
    const workspaceUrl = getLemmaApiBaseUrl();
    const target: AgentHostTarget | null = selectWorkspaceTarget(status?.targets ?? [], workspaceUrl);
    const pairedHere = Boolean(target);
    const hostId = target?.host_id ?? null;
    // Tell the paired-computer list which card is this machine, so it can label
    // that one instead of listing the same machine twice.
    useEffect(() => {
        onHostIdChange?.(hostId);
    }, [hostId, onHostIdChange]);

    // Outside the desktop app there is no "this computer" to speak of; the
    // section falls back to the download card instead.
    if (!isDesktop) return null;

    const state = describeThisComputer(status, error, workspaceUrl);

    const run = async (action: string, work: () => Promise<unknown>, success?: string) => {
        setBusy(action);
        try {
            await work();
            if (success) toast.success(success);
            // The shell answers on locald's event stream, so the status this
            // call could return was read before the change landed.
            setTimeout(() => void refetch(), 400);
        } catch (error) {
            toast.error(error instanceof Error ? error.message : String(error));
        } finally {
            setBusy(null);
        }
    };

    // The whole point of doing this in the desktop app: the code is minted with
    // the session already open on this page and handed straight to the bundled
    // sidecar, so nobody copies a command into a terminal.
    const connect = () =>
        run(
            'pair',
            async () => {
                const name = displayName.trim() || 'This computer';
                const pairing = await createPairing.mutateAsync({ displayName: name });
                await agentHostBridge.pair(getLemmaApiBaseUrl(), pairing.pairing_code, name);
                // Connecting by hand overrides any earlier "no".
                allowAutoConnect();
                onPaired?.();
            },
            'This computer is connected',
        );

    const disconnect = () => {
        void run(
            'unpair',
            async () => {
                // Explicit, so it has to stick: without this the next page load
                // pairs this computer straight back and Disconnect looks broken.
                declineAutoConnect();
                await agentHostBridge.unpair(target?.target_id ?? null);
                onPaired?.();
            },
            'This computer is disconnected',
        ).finally(() => setConfirmDisconnect(false));
    };

    const uptime = formatUptime(status?.uptime_seconds ?? null);

    return (
        <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
            <div className="flex flex-wrap items-start gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)]">
                    <Cpu className="size-4 text-[var(--text-secondary)]" />
                </span>
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-[var(--text-primary)]">This computer</span>
                        <StatusDot tone={state.tone} />
                        <span className="text-xs text-[var(--text-secondary)]">{state.label}</span>
                        {uptime && status?.running ? (
                            <span className="text-xs text-[var(--text-tertiary)]">· {uptime}</span>
                        ) : null}
                    </div>
                    <p className="mt-1 text-sm text-[var(--text-tertiary)]">{state.detail}</p>
                </div>

                {!status && error ? (
                    <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="gap-1.5"
                        loading={busy === 'retry'}
                        onClick={() => void run('retry', () => refetch())}
                    >
                        <RefreshCw className="size-3.5" />
                        Try again
                    </Button>
                ) : null}

                {status?.available && pairedHere ? (
                    <Button
                        type="button"
                        variant={status.running ? 'quiet' : 'primary'}
                        size="sm"
                        loading={busy === 'toggle'}
                        onClick={() =>
                            void run('toggle', () => {
                                // Turning it off is a decision too.
                                if (status.running) declineAutoConnect();
                                else allowAutoConnect();
                                return agentHostBridge.setEnabled(!status.running);
                            })
                        }
                    >
                        {status.running ? 'Turn off' : 'Turn on'}
                    </Button>
                ) : null}
            </div>

            {status?.available && !pairedHere ? (
                <div className="mt-3 flex flex-wrap items-end gap-2">
                    <Input
                        value={displayName}
                        onChange={(event) => setDisplayName(event.target.value)}
                        className="w-56"
                        aria-label="Name for this computer"
                    />
                    <Button
                        type="button"
                        size="sm"
                        loading={busy === 'pair'}
                        loadingLabel="Connecting"
                        onClick={() => void connect()}
                    >
                        Connect this computer
                    </Button>
                </div>
            ) : null}

            {pairedHere ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        className="gap-1.5"
                        loading={busy === 'refresh'}
                        onClick={() =>
                            void run('refresh', () => agentHostBridge.refresh(), 'Rechecking this computer')
                        }
                    >
                        <RefreshCw className="size-3.5" />
                        Recheck agents
                    </Button>
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        className="gap-1.5"
                        onClick={() => void run('log', () => agentHostBridge.openLog())}
                    >
                        <TerminalSquare className="size-3.5" />
                        View log
                    </Button>
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        onClick={() => setConfirmDisconnect(true)}
                        loading={busy === 'unpair'}
                    >
                        Disconnect
                    </Button>
                    <DestructiveConfirmationDialog
                        open={confirmDisconnect}
                        onOpenChange={setConfirmDisconnect}
                        title="Disconnect this computer?"
                        description="Its agents stop being available to this workspace."
                        resourceName="This computer"
                        confirmationText=""
                        consequences={['Pair it again from this page to bring them back.']}
                        confirmLabel="Disconnect"
                        pendingLabel="Disconnecting..."
                        isPending={busy === 'unpair'}
                        onConfirm={disconnect}
                    />
                </div>
            ) : null}

            {status?.running && (target?.pending_events ?? 0) > 0 ? (
                <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                    {target?.pending_events} update{target?.pending_events === 1 ? '' : 's'} waiting to
                    reach this workspace.
                </p>
            ) : null}
        </div>
    );
}
