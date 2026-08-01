'use client';

import { use } from 'react';
import { useSearchParams } from 'next/navigation';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { ModelsSettings } from '@/components/agents/models-settings';
import { InlineLoader } from '@/components/brand/loader';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { OrganizationSettingsNav } from '@/components/organizations/organization-settings-nav';
import { ProductIcon } from '@/components/pod/product-icon';
import { useAgentRuntimes } from '@/lib/hooks/use-agent-runtime';
import { normalizeInternalReturnPath } from '@/lib/navigation/settings-return';
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
    const searchParams = useSearchParams();
    const returnPath = normalizeInternalReturnPath(searchParams.get('returnTo'));
    const { data: organization } = useOrganizationDetails(organizationId);
    // ModelsSettings reads the management listing itself. This catalog query is
    // what the rest of the app (the composer's model picker) shares, so keep
    // refreshing it alongside — a rename here changes what that picker offers.
    const {
        data: runtimeCatalog,
        isLoading: isLoadingRuntimeCatalog,
        refetch: refetchRuntimeCatalog,
    } = useAgentRuntimes(organizationId);

    return (
        <PlainPageShell
            title="Models"
            icon={<ProductIcon kind="settings" size="sm" />}
            backHref={returnPath || '/home'}
            backLabel={returnPath ? 'Back to pod' : 'Home'}
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
                    onRefresh={() => {
                        void refetchRuntimeCatalog();
                    }}
                />
            </section>
        </PlainPageShell>
    );
}
