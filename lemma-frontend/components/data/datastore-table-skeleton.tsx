'use client';

import { Skeleton } from '@/components/shared/loading';

/**
 * DatastoreTableSkeleton — loading placeholder that mirrors the embedded
 * DatastoreTableView frame (toolbar + grid + footer) so the tables page loads
 * into the same shape it settles on, instead of flashing a floating dashed box.
 *
 * It reuses the `.data-table-workbench` CSS structure, so the toolbar/grid/footer
 * pick up the exact borders, radii, and spacing of the real table.
 */
const ROW_FADE = [
    'opacity-100',
    'opacity-90',
    'opacity-80',
    'opacity-70',
    'opacity-60',
    'opacity-50',
    'opacity-40',
];

export function DatastoreTableSkeleton({ rows = 8 }: { rows?: number }) {
    const columns = 4;

    return (
        <div
            className="datastore-table-workbench lemma-workbench-panel data-table-workbench relative flex h-full min-h-0 flex-col overflow-hidden"
            role="status"
            aria-label="Loading table"
        >
            <div className="data-table-toolbar shrink-0">
                <div className="flex w-full items-center justify-between gap-2">
                    <Skeleton shape="block" className="h-5 w-40" />
                    <div className="flex items-center gap-1.5">
                        <Skeleton shape="block" className="h-8 w-16" />
                        <Skeleton shape="block" className="h-8 w-20" />
                        <Skeleton shape="block" className="h-8 w-24" />
                    </div>
                </div>
            </div>

            <div className="data-table-viewport relative flex-1 overflow-hidden bg-[var(--row-bg)]">
                <div className="data-table-grid-frame h-full">
                    <div className="h-full overflow-hidden">
                        <div className="flex items-center gap-6 border-b border-[color:var(--row-border)] px-4 py-2.5">
                            {Array.from({ length: columns }).map((_, index) => (
                                <Skeleton key={index} shape="text" className="h-3.5 flex-1" />
                            ))}
                        </div>
                        <div className="divide-y divide-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)]">
                            {Array.from({ length: rows }).map((_, rowIndex) => (
                                <div
                                    key={rowIndex}
                                    className={`flex items-center gap-6 px-4 py-3 ${ROW_FADE[Math.min(rowIndex, ROW_FADE.length - 1)]}`}
                                >
                                    {Array.from({ length: columns }).map((_, columnIndex) => (
                                        <Skeleton key={columnIndex} shape="text" className="h-3 flex-1" />
                                    ))}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            <div className="data-table-footer shrink-0">
                <div className="flex items-center justify-between">
                    <Skeleton shape="text" className="h-3.5 w-48" />
                    <div className="flex items-center gap-2">
                        <Skeleton shape="block" className="h-8 w-20" />
                        <Skeleton shape="block" className="h-8 w-16" />
                    </div>
                </div>
            </div>
        </div>
    );
}
