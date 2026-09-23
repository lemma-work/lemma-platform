'use client';

import { useEffect, useCallback } from 'react';

interface UseTableShortcutsProps {
    onNewRecord?: () => void;
    onFilter?: () => void;
    onSearch?: () => void;
    enabled?: boolean;
}

export function useTableShortcuts({
    onNewRecord,
    onFilter,
    onSearch,
    enabled = true,
}: UseTableShortcutsProps) {
    const handleKeyDown = useCallback(
        (e: KeyboardEvent) => {
            if (!enabled) return;

            // Command/Ctrl + N - New record
            if ((e.metaKey || e.ctrlKey) && e.key === 'n') {
                e.preventDefault();
                onNewRecord?.();
            }

            // Command/Ctrl + F - Filter
            if ((e.metaKey || e.ctrlKey) && e.key === 'f') {
                e.preventDefault();
                onFilter?.();
            }

            // Command/Ctrl + K - Search
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                onSearch?.();
            }
        },
        [enabled, onNewRecord, onFilter, onSearch]
    );

    useEffect(() => {
        if (enabled) {
            window.addEventListener('keydown', handleKeyDown);
            return () => window.removeEventListener('keydown', handleKeyDown);
        }
    }, [enabled, handleKeyDown]);
}
