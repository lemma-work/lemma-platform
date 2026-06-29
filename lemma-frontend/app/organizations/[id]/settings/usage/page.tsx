'use client';

import { use } from 'react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { OrganizationSettingsNav } from '@/components/organizations/organization-settings-nav';
import { ProductIcon } from '@/components/pod/product-icon';
import { UsageOverview } from '@/components/usage/usage-overview';
import { useOrganizationDetails } from '@/lib/hooks/use-organizations';

export default function OrganizationUsagePage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <OrganizationUsagePageContent params={params} />
        </ProtectedRoute>
    );
}

function OrganizationUsagePageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: organizationId } = use(params);
    const { data: organization } = useOrganizationDetails(organizationId);

    return (
        <PlainPageShell
            title="Usage"
            icon={<ProductIcon tone="settings" size="sm" />}
            backHref="/"
            backLabel="Home"
            meta={organization?.name || 'Organization'}
            tabs={<OrganizationSettingsNav organizationId={organizationId} />}
            contentWidthClassName="max-w-6xl"
            contentClassName="pb-16 sm:pb-20"
        >
            <section className="office-arrive space-y-5">
                <UsageOverview
                    organizationId={organizationId}
                    scope="organization"
                    title={organization?.name ? `${organization.name} usage` : 'Organization usage'}
                />
            </section>
        </PlainPageShell>
    );
}
