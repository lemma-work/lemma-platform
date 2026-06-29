'use client';

import { use } from 'react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { ModelsSettings } from '@/components/agents/models-settings';
import { InlineLoader } from '@/components/brand/loader';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { OrganizationSettingsNav } from '@/components/organizations/organization-settings-nav';
import { ProductIcon } from '@/components/pod/product-icon';
import {
    useAgentRuntimes,
    useAvailableAgentRuntimeHarnesses,
} from '@/lib/hooks/use-agent-runtime';
import { useOrganizationDetails } from '@/lib/hooks/use-organizations';

export default function OrganizationAgentRuntimesPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <OrganizationAgentRuntimesPageContent params={params} />
        </ProtectedRoute>
    );
}

function OrganizationAgentRuntimesPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: organizationId } = use(params);
    const { data: organization } = useOrganizationDetails(organizationId);
    const {
        data: runtimeCatalog,
        isFetching: isFetchingRuntimeCatalog,
        isLoading: isLoadingRuntimeCatalog,
        refetch: refetchRuntimeCatalog,
    } = useAgentRuntimes(organizationId);
    const {
        data: availableHarnesses,
        isFetching: isFetchingAvailableHarnesses,
        refetch: refetchAvailableHarnesses,
    } = useAvailableAgentRuntimeHarnesses();

    return (
        <PlainPageShell
            title="Models"
            icon={<ProductIcon tone="settings" size="sm" />}
            backHref="/"
            backLabel="Home"
            meta={organization?.name || 'Organization'}
            tabs={<OrganizationSettingsNav organizationId={organizationId} />}
            contentWidthClassName="max-w-6xl"
            contentClassName="pb-16 sm:pb-20"
        >
            <section className="office-arrive settings-stack">
                {isLoadingRuntimeCatalog && !runtimeCatalog ? (
                    <div className="mb-3 flex h-10 items-center gap-2 rounded-md px-2 text-sm text-[var(--text-tertiary)]">
                        <InlineLoader size="xs" label="Loading models" />
                    </div>
                ) : null}
                <ModelsSettings
                    organizationId={organizationId}
                    catalog={runtimeCatalog}
                    availableHarnesses={availableHarnesses}
                    isRefreshing={isFetchingRuntimeCatalog || isFetchingAvailableHarnesses}
                    onRefresh={() => {
                        void refetchRuntimeCatalog();
                        void refetchAvailableHarnesses();
                    }}
                />
            </section>
        </PlainPageShell>
    );
}
