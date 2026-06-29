'use client';

import type { ReactNode } from 'react';

import { PodHeaderMetrics, PodPageHeader } from '@/components/pod/pod-page-header';
import { PodSettingsNav } from '@/components/pod/pod-settings-nav';
import { SettingsPanel } from '@/components/settings/settings-kit';

interface PodSettingsStat {
    label: string;
    value: string;
    detail?: string;
}

interface PodSettingsShellProps {
    podId: string;
    title: string;
    description: string;
    action?: ReactNode;
    stats?: PodSettingsStat[];
    children: ReactNode;
}

export function PodSettingsShell({
    podId,
    title,
    description,
    action,
    stats = [],
    children,
}: PodSettingsShellProps) {
    return (
        <div className="context-shell min-h-full bg-transparent">
            <section>
                <PodPageHeader
                    podId={podId}
                    showBack={false}
                    title={title}
                    description={description}
                    productIconTone="settings"
                    meta={stats.length > 0 ? <PodHeaderMetrics items={stats.map((stat) => ({ label: stat.label, value: stat.value }))} /> : undefined}
                    actions={action}
                    switcher={<PodSettingsNav podId={podId} />}
                />

            </section>

            {children}
        </div>
    );
}

/**
 * Back-compat alias. The panel now lives in the shared settings kit so pod and
 * org settings render the exact same card; prefer importing `SettingsPanel`
 * from '@/components/settings/settings-kit' in new code.
 */
export const PodSettingsPanel = SettingsPanel;
