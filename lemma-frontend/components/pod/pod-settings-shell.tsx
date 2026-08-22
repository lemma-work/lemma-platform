'use client';

import type { ReactNode } from 'react';

import { PodHeaderMetrics } from '@/components/pod/pod-page-header';
import { PodSettingsNav } from '@/components/pod/pod-settings-nav';
import { ResourceHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { SettingsPanel } from '@/components/settings/settings-kit';
import { cn } from '@/lib/utils';

interface PodSettingsStat {
    label: string;
    value: string;
    detail?: string;
}

interface PodSettingsShellProps {
    podId: string;
    /**
     * This tab's own name — "General", "Access" — not the area's. The context
     * bar prints it, and a bar that says "Pod Settings" on every route
     * tells you nothing you did not already know. The workspace tab keeps
     * reading "Settings" (see `tabTitle` below) because every route shares
     * one tab, and a tab that relabels as you move within it reads as churn.
     */
    title: string;
    /**
     * What this tab settles into.
     *
     * `'form'` for fields and choices, `'ledger'` for rows and charts. Not a
     * per-page number — the two page kinds genuinely want different measures,
     * and forcing one on both is how a lone model picker ends up floating in a
     * card two thirds of which is empty, with its selected-state check most of a
     * screen away from the label it belongs to.
     */
    width?: 'form' | 'ledger';
    action?: ReactNode;
    stats?: PodSettingsStat[];
    children: ReactNode;
}

/**
 * Chrome for the pod settings routes.
 *
 * Built on the same primitives as every ledger route — `ResourceIndexShell`
 * plus a declared `ResourceHeader` — so settings picks up shell changes with
 * everything else instead of drifting. It owns the content width, which is why
 * no settings page sets its own: a tab per width is the area's most
 * visible tell that nobody laid it out together.
 */
export function PodSettingsShell({
    podId,
    title,
    width = 'ledger',
    action,
    stats = [],
    children,
}: PodSettingsShellProps) {
    return (
        <ResourceIndexShell>
            <ResourceHeader
                title={title}
                tabTitle="Settings"
                meta={stats.length > 0 ? (
                    <PodHeaderMetrics items={stats.map((stat) => ({ label: stat.label, value: stat.value }))} />
                ) : undefined}
                actions={action}
            />

            {/*
             * One nav, in its own row under the bar — the same shape
             * organization settings uses (`PlainPageShell`'s tabs row). The
             * context bar's tab slot strands it against the right edge, half a
             * screen from the title it belongs to, and drops it entirely once
             * the bar runs out of room; settings is reached from the account
             * menu rather than the sidebar, so there is no other way back to
             * the sibling tabs.
             *
             * Sizing lives on this wrapper rather than on the nav.
             * `.lemma-header-tabs` and a Tailwind utility are both single
             * classes, so specificity ties and source order decides — and the
             * feature stylesheets are imported after Tailwind's, so the nav's
             * own rule wins. A `hidden`, `gap-*` or `p-*` handed to the nav for
             * a property `.lemma-header-tabs` already sets does nothing at all,
             * silently. (That is exactly how this row shipped twice: a
             * `sm:hidden` on a second nav that never hid.)
             */}
            <div className="mb-5 min-w-0 overflow-x-auto">
                <PodSettingsNav podId={podId} />
            </div>

            {/*
             * Left-aligned rather than centred, so the first character lands on
             * the same vertical as every other pod route however wide the tab is.
             */}
            <div className={cn('w-full', width === 'form' ? 'max-w-3xl' : 'max-w-6xl')}>
                {children}
            </div>
        </ResourceIndexShell>
    );
}

/**
 * Back-compat alias. The panel now lives in the shared settings kit so pod and
 * org settings render the exact same card; prefer importing `SettingsPanel`
 * from '@/components/settings/settings-kit' in new code.
 */
export const PodSettingsPanel = SettingsPanel;
