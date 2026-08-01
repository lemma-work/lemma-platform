'use client';

import { useState } from 'react';
import Image from 'next/image';
import { MessageCircle, Plus } from '@/components/ui/icons';

import { SurfaceModal, type SurfaceModalTarget } from '@/components/surfaces/surface-modal';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useAvailableSurfaces } from '@/lib/hooks/use-pod-surfaces';
import type { SurfacePlatformValue } from '@/lib/hooks/use-pod-surfaces';
import { getSurfaceDefinition, SURFACE_PLATFORM_ORDER } from '@/lib/surfaces/registry';
import {
    describeReach,
    getSurfaceDeepLink,
    getSurfaceIdentity,
    getSurfacePlatformKey,
    getSurfaceStatus,
} from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Where an agent is reachable — and the only place you change it.
 *
 * A live chip opens that surface's modal on its settings; a faded one starts a
 * new surface for that platform. Surfaces are independent, so a platform already
 * connected elsewhere in the pod is no reason to send someone to *that* surface:
 * this agent gets its own bot, routed to itself. Both stay on the page, because
 * the agent is the context that decides who answers.
 */
export function AgentSurfacesRow({
    podId,
    agentName,
    surfaces,
    label = 'Surfaces',
}: {
    podId: string;
    /** The agent these surfaces answer as; `null` = the pod default assistant. */
    agentName: string | null;
    /** Surfaces already reaching this agent. */
    surfaces: AssistantSurface[];
    /** Inline label. Pass `null` where a surrounding section already names it. */
    label?: string | null;
}) {
    const [target, setTarget] = useState<SurfaceModalTarget | null>(null);
    const { data: catalog } = useAvailableSurfaces(podId);

    const reached = new Set(surfaces.map((surface) => getSurfacePlatformKey(surface)));
    // Only offer platforms this deployment can actually run — a WhatsApp chip on
    // an install with no Meta credentials is a dead end.
    const connectable = SURFACE_PLATFORM_ORDER.filter((platform) => {
        if (reached.has(platform)) return false;
        if (!catalog) return true;
        const entry = catalog.find((row) => String(row.platform).toUpperCase() === platform);
        return entry ? entry.connector_available !== false : false;
    });

    return (
        <TooltipProvider>
            <div className="flex flex-wrap items-center gap-2">
                {label ? <span className="text-sm text-[var(--text-secondary)]">{label}</span> : null}

                {surfaces.map((surface) => (
                    <ReachChip
                        key={surface.id ?? surface.name}
                        surface={surface}
                        reachFor={agentName}
                        onOpen={() =>
                            setTarget({
                                platform: getSurfacePlatformKey(surface) as SurfacePlatformValue,
                                surfaceName: surface.name,
                            })
                        }
                    />
                ))}

                {connectable.map((platform) => (
                    <ConnectChip
                        key={platform}
                        platform={platform}
                        onOpen={() => setTarget({ platform })}
                    />
                ))}
            </div>

            <SurfaceModal
                podId={podId}
                target={target}
                agentName={agentName}
                onClose={() => setTarget(null)}
            />
        </TooltipProvider>
    );
}

const chipClass =
    'inline-flex max-w-[240px] items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] py-1 pl-1 pr-2.5 shadow-[var(--shadow-xs)] transition-colors hover:border-[var(--border-strong)]';

function ReachChip({
    surface,
    reachFor,
    onOpen,
}: {
    surface: AssistantSurface;
    reachFor: string | null;
    onOpen: () => void;
}) {
    const platform = getSurfacePlatformKey(surface);
    const definition = getSurfaceDefinition(platform);
    const status = getSurfaceStatus(surface);
    const identity = surface.reach?.handle || getSurfaceIdentity(surface);
    const deepLink = getSurfaceDeepLink(surface);

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button type="button" onClick={onOpen} className={cn('resource-chip-button', chipClass, 'custom-focus-ring')}>
                    <PlatformMark platform={platform} logoSrc={definition?.logoSrc} />
                    <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {identity || definition?.label || platform}
                    </span>
                    <span
                        className={cn(
                            'h-1.5 w-1.5 shrink-0 rounded-full',
                            status.tone === 'success'
                                ? 'bg-[var(--state-success)]'
                                : status.tone === 'warning' || status.tone === 'danger'
                                    ? 'bg-[var(--state-warning)]'
                                    : 'bg-[var(--text-tertiary)]',
                        )}
                        aria-hidden
                    />
                </button>
            </TooltipTrigger>
            <TooltipContent>
                {status.label} · {describeReach(surface, reachFor)}
                {deepLink ? ` · ${deepLink.replace(/^https?:\/\//, '')}` : ''}
            </TooltipContent>
        </Tooltip>
    );
}

function ConnectChip({
    platform,
    onOpen,
}: {
    platform: SurfacePlatformValue;
    onOpen: () => void;
}) {
    const definition = getSurfaceDefinition(platform);
    if (!definition) return null;

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <Button
                    type="button"
                    variant="quiet"
                    size="icon"
                    onClick={onOpen}
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-dashed border-[var(--border-subtle)] opacity-50 transition-opacity hover:opacity-100"
                    aria-label={`Connect ${definition.label}`}
                >
                    {definition.logoSrc ? (
                        <Image src={definition.logoSrc} alt="" width={16} height={16} className="object-contain" aria-hidden="true" />
                    ) : (
                        <Plus className="h-4 w-4 text-[var(--text-tertiary)]" aria-hidden />
                    )}
                </Button>
            </TooltipTrigger>
            <TooltipContent>{definition.connectHint}</TooltipContent>
        </Tooltip>
    );
}

function PlatformMark({ platform, logoSrc }: { platform: string; logoSrc?: string }) {
    return (
        <span
            className="surface-platform-mark surface-platform-mark-logo shrink-0"
            data-platform={platform.toLowerCase()}
        >
            {logoSrc ? (
                <Image src={logoSrc} alt="" width={16} height={16} className="surface-platform-logo" aria-hidden="true" />
            ) : null}
            <MessageCircle className="surface-platform-icon-fallback h-4 w-4" />
        </span>
    );
}
