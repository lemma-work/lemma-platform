'use client';

import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Cpu, RefreshCw, TerminalSquare } from '@/components/ui/icons';
import { agentHostBridge, type AgentHostTarget } from '@/lib/desktop/agent-host-bridge';
import { useAutoConnectThisComputer } from '@/lib/desktop/auto-connect';
import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';
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

/**
 * This machine's own row in Computers.
 *
 * It reports and it does not ask. Connecting is automatic
 * (`useAutoConnectThisComputer`, called here so the canonical surface performs
 * it too and not only the onboarding pages), there is no on/off switch, and
 * there is no Disconnect — removing the computer you are sitting at was undone
 * by the next page load unless a `localStorage` flag stopped it, and that flag
 * was a sixth thing that could disagree with the other five. Removing a machine
 * you are *not* at is `agent.host.revoke`, on its own card.
 *
 * What is left are the two things that are genuinely useful and are not
 * lifecycle: look for agents again, and read the log.
 */
export function ThisComputerCard({
    onHostIdChange,
    onPaired,
}: {
    onHostIdChange?: (hostId: string | null) => void;
    onPaired?: () => void;
}) {
    const { isDesktop, status, error, refetch } = useAutoConnectThisComputer();
    const [busy, setBusy] = useState<string | null>(null);

    // This workspace's pairing, not whichever one happens to be first: a Mac
    // paired to its own local stack and then opened against a hosted workspace
    // has two, and only one of them is what this card is about.
    const workspaceUrl = getLemmaApiBaseUrl();
    const target: AgentHostTarget | null = selectWorkspaceTarget(status?.targets ?? [], workspaceUrl);
    const hostId = target?.host_id ?? null;
    // Tell the paired-computer list which card is this machine, so it can label
    // that one instead of listing the same machine twice.
    useEffect(() => {
        onHostIdChange?.(hostId);
    }, [hostId, onHostIdChange]);

    // The automatic connection is the only thing that pairs this machine now, so
    // this is where "it just paired" is observed: the cloud list has to be
    // refetched to pick up a computer that was not there when the page loaded.
    const announced = useRef<string | null>(null);
    useEffect(() => {
        if (!hostId || announced.current === hostId) return;
        announced.current = hostId;
        onPaired?.();
    }, [hostId, onPaired]);

    // Outside the desktop app there is no "this computer" to speak of; the
    // section falls back to the download card instead.
    if (!isDesktop) return null;

    const state = describeThisComputer(status, error, workspaceUrl);
    const uptime = formatUptime(status?.uptime_seconds ?? null);

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
            </div>

            {status?.available ? (
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
