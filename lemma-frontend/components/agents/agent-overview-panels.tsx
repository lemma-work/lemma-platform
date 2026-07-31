'use client';

import type { ReactNode } from 'react';

import { AgentSurfacesRow } from '@/components/surfaces/agent-surfaces-row';
import type { AssistantSurface } from '@/lib/types';

/**
 * Rail parts for pages that present an agent-shaped thing in a side column.
 *
 * The agent detail page no longer has a rail — it states identity and wiring
 * inline — so what remains here is what the pod assistant still builds from.
 */

export function agentInitials(name: string): string {
    const tokens = name.trim().split(/[\s\-_]+/).filter(Boolean);
    if (tokens.length >= 2) return `${tokens[0][0]}${tokens[1][0]}`.toUpperCase();
    return (tokens[0] || name).slice(0, 2).toUpperCase();
}

export function AgentRailHeader({
    icon,
    label,
    description,
}: {
    icon: ReactNode;
    label: string;
    description?: string | null;
}) {
    const trimmed = description?.trim();
    return (
        <section className="pb-4">
            <div className="flex items-center gap-2.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--card-bg)] shadow-[var(--shadow-xs)]">
                    {icon}
                </span>
                <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--text-primary)]">
                    {label}
                </h2>
            </div>
            {trimmed ? (
                <p className="mt-2.5 text-xs leading-5 text-[var(--text-secondary)]">{trimmed}</p>
            ) : null}
        </section>
    );
}

export function AgentRailSection({ title, children }: { title: string; children: ReactNode }) {
    return (
        <section className="border-t border-[color:color-mix(in_srgb,var(--border-subtle)_54%,transparent)] py-4 first:border-t-0 first:pt-0">
            <h2 className="mb-2.5 text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
                {title}
            </h2>
            {children}
        </section>
    );
}

/** A state of affairs, not an instruction — used where a section is genuinely empty. */
export function AgentRailEmpty({ children }: { children: ReactNode }) {
    return <p className="text-sm leading-6 text-[var(--text-tertiary)]">{children}</p>;
}

/**
 * Where this agent can be reached, with the empty case said out loud.
 *
 * `AgentSurfacesRow` renders every connectable platform as a faded chip
 * alongside the live ones, which on its own reads as "five surfaces are set up".
 * The heading above it is what makes zero legible as zero.
 */
export function AgentSurfacesRail({
    podId,
    agentName,
    surfaces,
}: {
    podId: string;
    /** `null` = the pod default assistant. */
    agentName: string | null;
    surfaces: AssistantSurface[];
}) {
    const row = (
        <AgentSurfacesRow
            podId={podId}
            agentName={agentName}
            surfaces={surfaces}
            label={null}
        />
    );

    return (
        <AgentRailSection title="Surfaces">
            {surfaces.length === 0 ? (
                <div className="space-y-2.5">
                    <AgentRailEmpty>Nobody can reach it yet.</AgentRailEmpty>
                    {row}
                </div>
            ) : row}
        </AgentRailSection>
    );
}
