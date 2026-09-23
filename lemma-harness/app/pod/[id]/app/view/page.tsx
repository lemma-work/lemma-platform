'use client';

import { ProtectedRoute } from '@/components/auth/protected-route';

// The live app iframe is owned by the pod shell's keep-alive AppFrameHost, which
// is mounted above the router so it survives tab switches. This route only
// anchors the URL (?page=slug); the host reads the active slug from it and shows
// the matching (already-running) iframe. Keeping this a thin placeholder avoids
// mounting a second iframe that would cold-boot the app on every navigation.
export default function AppViewPage() {
    return (
        <ProtectedRoute>
            <div className="h-full w-full" />
        </ProtectedRoute>
    );
}
