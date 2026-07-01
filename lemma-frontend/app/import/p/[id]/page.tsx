'use client';

import { use, useEffect, useRef, useState } from 'react';
import { CircleAlert, Loader2 } from 'lucide-react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { ImportPodBundleWizard } from '@/components/pod/import-pod-bundle-wizard';
import { type PodImport, useImportFromPod } from '@/lib/hooks/use-pod-imports';

export default function ImportFromPodPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <ImportFromPodContent params={params} />
        </ProtectedRoute>
    );
}

function ImportFromPodContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: sourcePodId } = use(params);
    const importFromPod = useImportFromPod();
    const [imp, setImp] = useState<PodImport | null>(null);
    const [error, setError] = useState<string | null>(null);
    const started = useRef(false);

    useEffect(() => {
        if (started.current) return;
        started.current = true;
        importFromPod.mutate(
            { sourcePodId },
            {
                onSuccess: setImp,
                onError: (e) =>
                    setError(e instanceof Error ? e.message : 'Could not open this shared pod'),
            },
        );
    }, [sourcePodId, importFromPod]);

    if (error) {
        return (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-24 text-center">
                <CircleAlert className="h-6 w-6 text-[var(--state-error)]" />
                <p className="text-base font-medium text-[var(--text-primary)]">
                    Couldn&apos;t open this shared pod
                </p>
                <p className="text-sm text-[var(--text-secondary)]">{error}</p>
            </div>
        );
    }

    if (!imp) {
        return (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-24 text-center">
                <Loader2 className="h-6 w-6 animate-spin text-[var(--text-tertiary)]" />
                <p className="text-sm text-[var(--text-secondary)]">Preparing your copy…</p>
            </div>
        );
    }

    // The import targets a freshly-created pod; pass the source id as the context
    // pod so the wizard treats it as a new-pod import (imp.pod_id differs).
    return (
        <div className="py-8">
            <ImportPodBundleWizard podId={sourcePodId} initialImport={imp} />
        </div>
    );
}
