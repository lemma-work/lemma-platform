'use client';

import type { ReactNode } from 'react';

/**
 * One wiring question and its one-line answer.
 *
 * Agents, workflows, and Lem all answer the same shape of
 * question — what it can reach, who reaches it, when it runs — so the row lives
 * here rather than inside any one of them.
 */
export function WiringRow({
    label,
    children,
    action,
}: {
    label: string;
    children: ReactNode;
    action?: ReactNode;
}) {
    return (
        <div className="agent-wiring-row">
            <div className="agent-wiring-label">{label}</div>
            <div className="agent-wiring-value">{children}</div>
            {action ? <div className="agent-wiring-action">{action}</div> : null}
        </div>
    );
}

/** An answer of "none" — worth one quiet line, never a list of absences. */
export function Nothing({ children }: { children: ReactNode }) {
    return <span className="agent-wiring-empty">{children}</span>;
}
