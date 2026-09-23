'use client';

import { useCallback, useEffect, useState } from 'react';

import type { Column } from '@/lib/types';

/**
 * How wide a grid column is.
 *
 * A column is furniture with a width, not a function of whatever the widest
 * value in it happens to be. Letting the content decide means one long note
 * pushes every other column off the screen, and the table a reader came to scan
 * becomes a table they have to scroll sideways through to read one row.
 *
 * So each column starts at a width its type usually needs, and the reader can
 * drag it to whatever they actually want. What they drag it to is theirs to
 * keep: the widths persist per table, in this browser, because a column width
 * is a reading preference and not something to publish to everyone in the pod.
 */

export const MIN_COLUMN_WIDTH = 72;
export const MAX_COLUMN_WIDTH = 720;

/**
 * The row-select checkbox and the trailing expand/add-column rail. Both are
 * sized to their control plus its padding — the cells clip now, so a rail that
 * is a few pixels short of its own button would shave the button.
 */
export const SELECT_COLUMN_WIDTH = 40;
export const ACTIONS_COLUMN_WIDTH = 48;

/**
 * A checkbox never needs the room a paragraph does. One uniform default would
 * spend the same span on `is_active` as on `summary`, which wastes the screen
 * at one end and starves it at the other.
 *
 * The narrow end is set by the header rather than the values: a boolean's
 * control fits in half of what is here, but `is_archived` has to stay readable
 * above it, and a column whose own name is elided is a column nobody can scan.
 */
const WIDTH_BY_TYPE: Record<string, number> = {
    BOOLEAN: 116,
    SERIAL: 116,
    INTEGER: 132,
    FLOAT: 132,
    DATE: 148,
    ENUM: 160,
    USER: 168,
    LINK: 176,
    DATETIME: 184,
    UUID: 200,
    FILE_PATH: 224,
    TEXT: 232,
    JSON: 240,
    VECTOR: 240,
};

const FALLBACK_WIDTH = 200;

function clampWidth(width: number): number {
    return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, Math.round(width)));
}

export function defaultColumnWidth(column: Column): number {
    return WIDTH_BY_TYPE[String(column.type).toUpperCase()] ?? FALLBACK_WIDTH;
}

function storageKeyFor(podId: string, tableName: string): string {
    return `lemma.table-column-widths.${podId}.${tableName}`;
}

function readStoredWidths(key: string): Record<string, number> {
    if (typeof window === 'undefined') return {};

    try {
        const raw = window.localStorage.getItem(key);
        if (!raw) return {};

        const parsed: unknown = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};

        return Object.entries(parsed as Record<string, unknown>).reduce<Record<string, number>>(
            (accumulator, [name, value]) => {
                if (typeof value === 'number' && Number.isFinite(value)) {
                    accumulator[name] = clampWidth(value);
                }
                return accumulator;
            },
            {}
        );
    } catch {
        // A browser with storage denied, or a stale value written by an older
        // shape, is not a reason to fail to draw the table. Fall back to the
        // per-type defaults.
        return {};
    }
}

function writeStoredWidths(key: string, widths: Record<string, number>): void {
    if (typeof window === 'undefined') return;

    try {
        if (Object.keys(widths).length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(widths));
    } catch {
        // Same reasoning as the read: the widths are a convenience, so a full or
        // blocked store costs this session's resizes and nothing else.
    }
}

export function useTableColumnWidths(podId: string, tableName: string) {
    const [widths, setWidths] = useState<Record<string, number>>({});

    // Deliberately an effect and not a lazy initializer: this component renders
    // on the server too, where `localStorage` does not exist, and seeding state
    // from it would make the first client render disagree with the server's.
    useEffect(() => {
        setWidths(readStoredWidths(storageKeyFor(podId, tableName)));
    }, [podId, tableName]);

    const widthFor = useCallback(
        (column: Column): number => widths[column.name] ?? defaultColumnWidth(column),
        [widths]
    );

    const setColumnWidth = useCallback(
        (columnName: string, width: number) => {
            setWidths((previous) => {
                const next = { ...previous, [columnName]: clampWidth(width) };
                writeStoredWidths(storageKeyFor(podId, tableName), next);
                return next;
            });
        },
        [podId, tableName]
    );

    const resetColumnWidth = useCallback(
        (columnName: string) => {
            setWidths((previous) => {
                if (!(columnName in previous)) return previous;

                const next = { ...previous };
                delete next[columnName];
                writeStoredWidths(storageKeyFor(podId, tableName), next);
                return next;
            });
        },
        [podId, tableName]
    );

    return { widthFor, setColumnWidth, resetColumnWidth, clampWidth };
}
