'use client';

import { use, useEffect } from 'react';
import { useRouter } from 'next/navigation';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { InlineLoader } from '@/components/brand/loader';
import { podModelsHref } from '@/lib/navigation/pod-settings';
import { useAccessiblePods } from '@/lib/hooks/use-pods';

/**
 * Models moved into the pod. This route only forwards.
 *
 * A provider key is still stored against the organization — one key, billed and
 * rotated once — but that is where the rows live, not where they are read. The
 * page that reads them is the pod's Models tab, because every question you
 * bring to it ("what does this chat run on", "why can't this agent reach my
 * laptop") is asked from inside a pod. Kept as a redirect rather than deleted
 * so bookmarks and any in-flight link land somewhere useful.
 */
export default function OrganizationAgentRuntimesPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <OrganizationAgentRuntimesRedirect params={params} />
        </ProtectedRoute>
    );
}

function OrganizationAgentRuntimesRedirect({ params }: { params: Promise<{ id: string }> }) {
    const { id: organizationId } = use(params);
    const router = useRouter();
    const { data, isLoading } = useAccessiblePods();

    // The most recently touched pod in this organization — the same one the
    // sidebar would open. An organization with no pod yet has no model to run
    // anywhere, so home (where a pod is created) is the honest destination.
    const pods = data.groups.find((group) => group.organization.id === organizationId)?.pods ?? [];
    const target = pods[0] ? podModelsHref(pods[0].id) : '/home';

    useEffect(() => {
        if (isLoading) return;
        router.replace(target);
    }, [isLoading, router, target]);

    return (
        <div className="flex min-h-[60vh] items-center justify-center">
            <InlineLoader label="Opening models" />
        </div>
    );
}
