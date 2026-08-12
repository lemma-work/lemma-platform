'use client';

import { Suspense, use } from 'react';

import { NotificationsView } from '@/components/notifications/notifications-view';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { ResourceHeader, ResourceIndexShell } from '@/components/pod/resource-layout';

/**
 * Everything the pod has asked of you, in one place.
 *
 * The bell popover stays, but only as a peek — a 22rem column was being asked
 * to hold a title, a whole body, a meta line and three buttons per row, which
 * is unreadable at six rows and impossible past that. Anything that needs
 * reading, answering or looking back through happens here.
 */
export default function PodNotificationsPage({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);

    return (
        <ProtectedRoute>
            <ResourceIndexShell>
                <ResourceHeader title="Notifications" />
                {/* `useSearchParams` in the view — the deep link from a peek — puts
                    this subtree behind a boundary at build time. */}
                <Suspense fallback={null}>
                    <NotificationsView podId={podId} />
                </Suspense>
            </ResourceIndexShell>
        </ProtectedRoute>
    );
}
