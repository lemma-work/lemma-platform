'use client';

import { useMemo, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Loader2, MessageCircle } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { formatAgentName } from '@/lib/utils/agents';
import { SURFACE_PLATFORM_META, getSurfacePlatformKey } from '@/lib/utils/surfaces';
import { useUpdatePodSurface } from '@/lib/hooks/use-pod-surfaces';
import type { AssistantSurface } from '@/lib/types';

type ChannelFormMode = 'connect' | 'route';

// Connect a brand-new surface (an OAuth/setup flow that can't be inlined, so it
// hands off to the surfaces page) or route an already-connected one to this
// agent (or, when `agentName` is null, back to the pod's default assistant).
// "Connect new" is the default view — most people opening this want to add a
// channel, not re-route one that's already live.
export function InlineChannelForm({
    podId,
    agentName,
    allSurfaces,
    manageHref,
    onDone,
    onCancel,
}: {
    podId: string;
    /** Agent to route surfaces to, or `null` for the pod's default assistant. */
    agentName: string | null;
    allSurfaces: AssistantSurface[];
    manageHref: string;
    onDone: () => void;
    onCancel: () => void;
}) {
    const updateSurface = useUpdatePodSurface();
    const [mode, setMode] = useState<ChannelFormMode>('connect');

    // Connected surfaces that don't already answer here. Addressed by name,
    // not platform — a pod can have several surfaces of the same platform
    // (different bots/accounts), each routed to its own agent.
    const assignable = useMemo(
        () => allSurfaces.filter((surface) => (surface.agent_name ?? null) !== agentName),
        [allSurfaces, agentName],
    );

    const [surfaceName, setSurfaceName] = useState('');
    const selectedSurfaceName = surfaceName || assignable[0]?.name || '';

    const handleAssign = async () => {
        if (!selectedSurfaceName) return;
        try {
            await updateSurface.mutateAsync({
                podId,
                surfaceName: selectedSurfaceName,
                data: { default_agent_name: agentName },
            });
            toast.success(agentName ? 'Channel routed to this agent' : 'Channel routed to the pod default');
            onDone();
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Failed to route channel');
        }
    };

    return (
        <div>
            <div className="mb-3 flex items-center gap-2">
                {(['connect', 'route'] as ChannelFormMode[]).map((option) => (
                    <button
                        key={option}
                        type="button"
                        onClick={() => setMode(option)}
                        className="choice-chip choice-chip-sm"
                        data-active={mode === option ? 'true' : undefined}
                    >
                        {option === 'connect' ? 'Connect new' : 'Route existing'}
                    </button>
                ))}
            </div>

            {mode === 'connect' ? (
                <div>
                    <p className="text-sm text-[var(--text-secondary)]">
                        Pick a platform to connect — account setup finishes on the surfaces page.
                    </p>
                    <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {Object.entries(SURFACE_PLATFORM_META).map(([key, meta]) => (
                            <li key={key}>
                                <Link
                                    href={manageHref}
                                    className="flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] px-2.5 py-2 text-sm font-medium text-[var(--text-primary)] transition-colors hover:border-[var(--border-strong)]"
                                >
                                    <span className="surface-platform-mark surface-platform-mark-logo shrink-0" data-platform={key.toLowerCase()}>
                                        {meta.logoSrc ? (
                                            <Image src={meta.logoSrc} alt="" width={16} height={16} className="surface-platform-logo" aria-hidden="true" />
                                        ) : null}
                                        <MessageCircle className="surface-platform-icon-fallback h-4 w-4" />
                                    </span>
                                    <span className="truncate">{meta.label}</span>
                                </Link>
                            </li>
                        ))}
                    </ul>
                    <div className="mt-3 flex justify-end">
                        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
                    </div>
                </div>
            ) : assignable.length === 0 ? (
                <div>
                    <p className="text-sm text-[var(--text-secondary)]">
                        No connected surfaces to route here yet.
                    </p>
                    <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                        Connect a surface first, then route it to {agentName ? formatAgentName(agentName) : 'the pod default'}.
                    </p>
                    <div className="mt-3 flex items-center gap-2">
                        <Button type="button" size="sm" variant="outline" onClick={() => setMode('connect')}>Connect a surface</Button>
                        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
                    </div>
                </div>
            ) : (
                <div>
                    <div className="space-y-1">
                        <Label className="text-xs">Route a connected surface to {agentName ? formatAgentName(agentName) : 'the pod default'}</Label>
                        <Select value={selectedSurfaceName} onValueChange={setSurfaceName}>
                            <SelectTrigger>
                                <SelectValue placeholder="Choose a surface" />
                            </SelectTrigger>
                            <SelectContent>
                                {assignable.map((surface) => {
                                    const key = getSurfacePlatformKey(surface);
                                    const meta = SURFACE_PLATFORM_META[key];
                                    const current = surface.agent_name ? `now: ${formatAgentName(surface.agent_name)}` : 'now: pod default';
                                    return (
                                        <SelectItem key={surface.id} value={surface.name}>
                                            {(meta?.label ?? key)} · {current}
                                        </SelectItem>
                                    );
                                })}
                            </SelectContent>
                        </Select>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-[var(--text-tertiary)]">
                        {agentName ? 'This agent' : 'The pod default'} becomes the responder for the surface&apos;s direct messages. Per-channel routing and account setup live in{' '}
                        <Link href={manageHref} className="font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]">surfaces</Link>.
                    </p>
                    <div className="mt-3 flex items-center justify-end gap-2">
                        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={updateSurface.isPending}>Cancel</Button>
                        <Button type="button" size="sm" className="gap-1.5" onClick={() => void handleAssign()} disabled={updateSurface.isPending || !selectedSurfaceName}>
                            {updateSurface.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                            Route here
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
