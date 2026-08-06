'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Download, LayoutGrid, Plus } from '@/components/ui/icons';
import { ImportDialog } from '@/components/bundle/import-dialog';
import { Button } from '@/components/ui/button';
import { useAccessiblePods } from '@/lib/hooks/use-pods';

/**
 * The first-run screen.
 *
 * It used to be a grey box reading "No pods yet" beside a single button, on a
 * product that also ships templates and bundle import — so the two starting
 * moves that need no blank page were the two it never mentioned.
 *
 * The list header stands its create button down when this renders, so the
 * primary here is the only one on the page.
 */
export function EmptyPodsState() {
    const { data } = useAccessiblePods();
    const organizationId = data.organizations[0]?.id;
    const [isImportOpen, setIsImportOpen] = useState(false);

    return (
        <>
            <div className="surface-panel-muted px-4 py-5">
                <p className="text-sm font-medium text-[var(--text-primary)]">No pods yet</p>
                <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    Start with the work loop you want Lemma to help operate.
                </p>
                <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
                    <Button variant="primary" asChild size="sm" className="gap-2 px-4">
                        <Link href="/create-pod">
                            <Plus className="h-4 w-4" />
                            New Pod
                        </Link>
                    </Button>
                    <Button variant="secondary" asChild size="sm" className="gap-2 px-4">
                        <Link href="/templates">
                            <LayoutGrid className="h-4 w-4" />
                            Browse templates
                        </Link>
                    </Button>
                    {organizationId ? (
                        <Button
                            variant="secondary"
                            size="sm"
                            className="gap-2 px-4"
                            onClick={() => setIsImportOpen(true)}
                        >
                            <Download className="h-4 w-4" />
                            Import a pod
                        </Button>
                    ) : null}
                </div>
            </div>
            {organizationId ? (
                <ImportDialog
                    open={isImportOpen}
                    onOpenChange={setIsImportOpen}
                    createNew={{ organizationId }}
                />
            ) : null}
        </>
    );
}
