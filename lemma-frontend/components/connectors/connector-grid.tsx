'use client';

import { useMemo } from 'react';

import { EmptyState } from '@/components/shared/empty-state';
import { Plug } from '@/components/ui/icons';
import type { Connector } from '@/lib/types';
import { ConnectorRow } from './connector-card';
import { groupConnectors } from './connector-categories';
import { hasAdvancedOptions } from './connector-utils';

export function ConnectorGrid({
    connectors,
    connectedAppIds,
    busyAppId,
    searchTerm,
    onConnect,
    onAdvanced,
}: {
    connectors: Connector[];
    connectedAppIds: Set<string>;
    busyAppId: string | null;
    searchTerm: string;
    onConnect: (app: Connector) => void;
    onAdvanced: (app: Connector) => void;
}) {
    const sections = useMemo(() => groupConnectors(connectors), [connectors]);

    if (connectors.length === 0) {
        return (
            <EmptyState
                variant="region"
                icon={<Plug className="h-4 w-4" />}
                title="No connectors match this search"
                description={`Try a different app name${searchTerm ? ` than "${searchTerm}"` : ''}.`}
            />
        );
    }

    return (
        <div className="flex flex-col gap-8">
            {sections.map((section) => (
                <section key={section.title}>
                    <div className="mb-1 flex items-baseline gap-2">
                        <h3 className="text-sm font-medium text-[var(--text-primary)]">{section.title}</h3>
                        <span className="text-xs text-[var(--text-tertiary)]">{section.connectors.length}</span>
                    </div>
                    {section.blurb ? (
                        <p className="mb-2 text-xs text-[var(--text-tertiary)]">{section.blurb}</p>
                    ) : null}
                    <div className="grid grid-cols-1 gap-x-4 lg:grid-cols-2">
                        {section.connectors.map((app) => (
                            <ConnectorRow
                                key={app.id}
                                app={app}
                                isConnected={connectedAppIds.has(app.id)}
                                isBusy={busyAppId === app.id}
                                hasAdvanced={hasAdvancedOptions(app)}
                                onConnect={onConnect}
                                onAdvanced={onAdvanced}
                            />
                        ))}
                    </div>
                </section>
            ))}
        </div>
    );
}
