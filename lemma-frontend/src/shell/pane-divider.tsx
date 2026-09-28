import { useState } from "react";
import { clampPaneWidth } from "./split-tabs";

export function PaneDivider({ value, onChange }: { value: number; onChange: (value: number) => void }) {
    const [dragging, setDragging] = useState(false);
    return <>
        {dragging && <div className="pane-resize-shield" />}
        <div
            className={"pane-divider" + (dragging ? " pane-divider--dragging" : "")}
            role="separator"
            aria-label="Resize sidebar"
            aria-orientation="vertical"
            aria-valuemin={35}
            aria-valuemax={65}
            aria-valuenow={Math.round(value)}
            aria-valuetext={Math.round(100 - value) + "% sidebar width"}
            tabIndex={0}
            onPointerDown={event => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.currentTarget.focus();
                event.currentTarget.setPointerCapture(event.pointerId);
                setDragging(true);
            }}
            onPointerMove={event => {
                if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
                const bounds = event.currentTarget.parentElement?.getBoundingClientRect();
                if (bounds?.width) onChange(clampPaneWidth((event.clientX - bounds.left) / bounds.width * 100));
            }}
            onPointerUp={event => {
                if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
                setDragging(false);
            }}
            onPointerCancel={() => setDragging(false)}
            onLostPointerCapture={() => setDragging(false)}
            onDoubleClick={() => onChange(52)}
            onKeyDown={event => {
                const next = event.key === "ArrowLeft" ? value - 2
                    : event.key === "ArrowRight" ? value + 2
                    : event.key === "Home" ? 35
                    : event.key === "End" ? 65 : null;
                if (next === null) return;
                event.preventDefault();
                onChange(clampPaneWidth(next));
            }}
        />
    </>;
}
