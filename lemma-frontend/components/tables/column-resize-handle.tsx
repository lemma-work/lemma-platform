'use client';

import { useRef, useState } from 'react';

/**
 * The grip on a column's right edge.
 *
 * Drag moves the edge; double-click hands the column back to its default. The
 * drag itself never goes through React state — a table holds a page of records,
 * and re-rendering every cell on every pointer move would make the edge lag the
 * pointer it is supposed to be following. `onPreview` writes the width straight
 * to the `<col>` element, and only the width the reader lets go at is committed.
 */
interface ColumnResizeHandleProps {
    columnName: string;
    /** The column's width when the drag starts, in pixels. */
    width: number;
    onPreview: (columnName: string, width: number) => void;
    onCommit: (columnName: string, width: number) => void;
    onReset: (columnName: string) => void;
}

const KEYBOARD_STEP = 16;

export function ColumnResizeHandle({
    columnName,
    width,
    onPreview,
    onCommit,
    onReset,
}: ColumnResizeHandleProps) {
    const dragStart = useRef<{ pointerX: number; width: number } | null>(null);
    const [isDragging, setIsDragging] = useState(false);

    const widthAt = (pointerX: number): number => {
        const start = dragStart.current;
        if (!start) return width;
        return start.width + (pointerX - start.pointerX);
    };

    return (
        <div
            role="separator"
            aria-orientation="vertical"
            aria-label={`Resize ${columnName} column`}
            tabIndex={0}
            data-dragging={isDragging ? 'true' : undefined}
            className="data-table-column-resizer"
            // The whole header cell sorts on click. Without this the grip would
            // re-sort the table every time someone finished widening a column.
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={(event) => {
                event.stopPropagation();
                onReset(columnName);
            }}
            onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.stopPropagation();
                dragStart.current = { pointerX: event.clientX, width };
                event.currentTarget.setPointerCapture(event.pointerId);
                setIsDragging(true);
            }}
            onPointerMove={(event) => {
                if (!dragStart.current) return;
                onPreview(columnName, widthAt(event.clientX));
            }}
            onPointerUp={(event) => {
                if (!dragStart.current) return;
                const next = widthAt(event.clientX);
                dragStart.current = null;
                event.currentTarget.releasePointerCapture(event.pointerId);
                setIsDragging(false);
                onCommit(columnName, next);
            }}
            onPointerCancel={() => {
                dragStart.current = null;
                setIsDragging(false);
                onCommit(columnName, width);
            }}
            onKeyDown={(event) => {
                if (event.key === 'ArrowLeft') {
                    event.preventDefault();
                    onCommit(columnName, width - KEYBOARD_STEP);
                } else if (event.key === 'ArrowRight') {
                    event.preventDefault();
                    onCommit(columnName, width + KEYBOARD_STEP);
                } else if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onReset(columnName);
                }
            }}
        />
    );
}
