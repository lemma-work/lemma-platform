'use client';

import { EmptyState } from '@/components/shared/empty-state';
import { Plug } from 'lucide-react';
import type { Connector } from '@/lib/types';
import { ConnectorCard } from './connector-card';
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
    return (
        <div className="resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
            {connectors.map((app) => (
                <ConnectorCard
                    key={app.id}
                    app={app}
                    isConnected={connectedAppIds.has(app.id)}
                    isBusy={busyAppId === app.id}
                    hasAdvanced={hasAdvancedOptions(app)}
                    onConnect={onConnect}
                    onAdvanced={onAdvanced}
                />
            ))}

            {connectors.length === 0 && (
                <EmptyState
                    variant="panel"
                    icon={<Plug className="h-4 w-4" />}
                    title="No connectors match this search"
                    description={`Try a different app name${searchTerm ? ` than "${searchTerm}"` : ''}.`}
                    className="col-span-full"
                />
            )}
        </div>
    );
}
