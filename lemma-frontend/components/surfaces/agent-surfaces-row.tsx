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
    surfaceReaches,
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
 *
 * Slack and Teams are the exception, and the reason `channelRoutes` exists. One
 * workspace install reaches this agent in as many channels as you route to it,
 * so the install is not the unit anyone thinks in — the channel is. Those
 * surfaces render a chip per channel and keep offering "add another", where an
 * identity platform renders one chip and stops.
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
    // an install with no Meta credentials is a dead end. A channel platform that
    // is already installed keeps its own "add channel" chip beside its channels
    // instead, so it drops out here either way.
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
                    <SurfaceChips
                        key={surface.id ?? surface.name}
                        surface={surface}
                        reachFor={agentName}
                        onOpen={(intent) =>
                            setTarget({
                                platform: getSurfacePlatformKey(surface) as SurfacePlatformValue,
                                surfaceName: surface.name,
                                ...(intent ? { intent } : {}),
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

/**
 * One surface's chips: a single chip for an identity platform, one per channel
 * for a channel platform.
 *
 * There is no chip for "someone else holds the DMs" any more. On Slack each
 * person picks the agent that answers their own DMs, from the App Home, so DMs
 * are not a thing one agent takes from the others — an agent that holds none
 * simply has no DM chip, which is the same as any other reach it lacks.
 */
function SurfaceChips({
    surface,
    reachFor,
    onOpen,
}: {
    surface: AssistantSurface;
    reachFor: string | null;
    onOpen: (intent?: 'add-channel') => void;
}) {
    const platform = getSurfacePlatformKey(surface);
    const definition = getSurfaceDefinition(platform);

    if (!definition?.capabilities.channelRoutes) {
        return <ReachChip surface={surface} reachFor={reachFor} onOpen={() => onOpen()} />;
    }

    const reaches = surfaceReaches(surface, reachFor);

    return (
        <>
            {reaches.map((reach) => (
                <ReachChip
                    key={reach.key}
                    surface={surface}
                    reachFor={reachFor}
                    labelOverride={reach.label}
                    detail={reach.detail}
                    onOpen={() => onOpen()}
                />
            ))}

            <AddChannelChip
                platformLabel={definition.label}
                // Named, because a pod can hold two workspaces of the same
                // platform and then this chip appears twice — identical on the
                // face of it, pointing at different installs.
                workspaceLabel={
                    surface.reach?.handle || getSurfaceIdentity(surface) || definition.label
                }
                logoSrc={definition.logoSrc}
                onOpen={() => onOpen('add-channel')}
            />
        </>
    );
}

const chipClass =
    'inline-flex max-w-[240px] items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] py-1 pl-1 pr-2.5 shadow-[var(--shadow-xs)] transition-colors hover:border-[var(--border-strong)]';

function ReachChip({
    surface,
    reachFor,
    labelOverride,
    detail,
    onOpen,
}: {
    surface: AssistantSurface;
    reachFor: string | null;
    /** What this chip stands for, when it isn't the whole surface — `#sales`. */
    labelOverride?: string;
    /** Why this reach exists, when the label doesn't say. */
    detail?: string;
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
                        {labelOverride || identity || definition?.label || platform}
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
                {/* A chip standing for one channel already says what it reaches, so
                    the tooltip spends itself on what the chip dropped: which
                    workspace that channel lives in, and — for DMs, where reach is
                    now per person — how this agent came to hold any. */}
                {status.label} · {labelOverride
                    ? identity || definition?.label || platform
                    : describeReach(surface, reachFor)}
                {detail ? ` · ${detail}` : ''}
                {deepLink ? ` · ${deepLink.replace(/^https?:\/\//, '')}` : ''}
            </TooltipContent>
        </Tooltip>
    );
}

/**
 * Adds another channel to a workspace that is already installed.
 *
 * Labelled rather than icon-only, because this is the affordance the old row
 * hid: once a platform was reached its connect chip disappeared, which is right
 * for a number and wrong for a workspace whose whole point is that channels are
 * additive.
 */
function AddChannelChip({
    platformLabel,
    workspaceLabel,
    logoSrc,
    onOpen,
}: {
    platformLabel: string;
    /** Which install this adds to — the workspace handle, or the platform name
     * when the surface has no resolved reach yet. */
    workspaceLabel: string;
    logoSrc?: string;
    onOpen: () => void;
}) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    type="button"
                    onClick={onOpen}
                    // The visible text is deliberately short; the label carries the
                    // workspace, so two of these chips are distinguishable without
                    // hovering either.
                    aria-label={`Add a ${platformLabel} channel from ${workspaceLabel}`}
                    className={cn(
                        'resource-chip-button custom-focus-ring inline-flex items-center gap-1.5 rounded-lg border border-dashed border-[var(--border-subtle)] py-1 pl-2 pr-2.5 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]',
                    )}
                >
                    {logoSrc ? (
                        <Image src={logoSrc} alt="" width={14} height={14} className="object-contain opacity-70" aria-hidden="true" />
                    ) : (
                        <Plus className="h-3.5 w-3.5" aria-hidden />
                    )}
                    Add channel
                </button>
            </TooltipTrigger>
            <TooltipContent>
                Route another channel in {workspaceLabel} to this agent. It answers there
                when mentioned, or in a thread it already joined.
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
