'use client';

import { useState } from 'react';
import { Download } from '@/components/ui/icons';

import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { ImportDialog } from './import-dialog';

/**
 * "Import a pod" entry for the home sidebar — creates a brand-new pod from a
 * bundle (a .zip or a public GitHub repo) in the user's default workspace.
 */
export function HomeImportButton({ onNavigate }: { onNavigate?: () => void }) {
    const [open, setOpen] = useState(false);
    const { data } = useAccessiblePods();
    const orgId = data.organizations[0]?.id;

    if (!orgId) return null;

    return (
        <>
            <button
                type="button"
                onClick={() => {
                    onNavigate?.();
                    setOpen(true);
                }}
                className="lemma-sidebar-row lemma-sidebar-row-comfy w-full text-left"
            >
                <Download className="h-4 w-4" />
                Import a pod
            </button>
            <ImportDialog open={open} onOpenChange={setOpen} createNew={{ organizationId: orgId }} />
        </>
    );
}
