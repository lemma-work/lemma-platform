'use client';

import { Skeleton } from '@/components/shared/loading';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface TableTabStripProps {
    tables: { name: string }[];
    activeTableName: string | null;
    loadingTables: boolean;
    onSelectTable: (tableName: string) => void;
}

/**
 * Every table in the pod, on one line, with the open one underlined.
 *
 * This replaces a dropdown hung off the page *title*. A title is not a control:
 * nothing about "Projects ⌄" said other tables existed, and the same slot meant
 * "folder" on the files side, so the one visible switcher on the screen changed
 * meaning depending on which tab you were in.
 *
 * It renders inside the table toolbar rather than above it — "which table" and
 * "what can I do to it" belong on one line. Creating a table is the page
 * header's job, so there is no `+` here; two create buttons a centimetre apart
 * is not discoverability.
 *
 * Tabs take `lemma-index-tab` — the same underline the Workflows and Functions
 * ledgers use — rather than a second tab look invented for this page. No counts:
 * the tables list has no row totals in it, and a strip of em-dashes is worse
 * than no column at all.
 */
export function TableTabStrip({
    tables,
    activeTableName,
    loadingTables,
    onSelectTable,
}: TableTabStripProps) {
    if (loadingTables && tables.length === 0) {
        return (
            <div className="data-table-strip flex min-w-0 items-center gap-5">
                {[0, 1, 2].map((index) => (
                    <Skeleton key={index} shape="block" className="h-3.5 w-20" />
                ))}
            </div>
        );
    }

    if (tables.length === 0) return null;

    // Not `role="tablist"`: ARIA tabs promise arrow-key roving focus, and these
    // are links-by-another-name that push a route. `aria-current` says which one
    // you are on without claiming keyboard behaviour we don't implement — the
    // same call `ResourceMetricButton` makes.
    return (
        <div className="data-table-strip flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="Tables">
            {tables.map((item) => {
                const isActive = item.name === activeTableName;
                return (
                    <Button
                        key={item.name}
                        variant="quiet"
                        aria-current={isActive ? 'true' : undefined}
                        data-active={isActive}
                        onClick={() => onSelectTable(item.name)}
                        className={cn('lemma-index-tab shrink-0', isActive && 'font-medium')}
                    >
                        <span className="max-w-[14rem] truncate">{item.name}</span>
                    </Button>
                );
            })}
        </div>
    );
}
