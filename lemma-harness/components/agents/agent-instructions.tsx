'use client';

import { useEffect, useState } from 'react';

import { MarkdownEditor } from '@/components/documents/markdown-editor';
import type { Agent } from '@/lib/types';

/**
 * The prompt, as the body of the page rather than a panel inside it.
 *
 * This is where nearly all the work on an agent happens, so it gets the width
 * and the quiet. Edits land on the draft after a pause; the page above owns
 * saving them.
 */

function normalize(value: string) {
    return value.replace(/[ \t]+$/gm, '').replace(/\n{3,}$/g, '\n\n').replace(/\s+$/g, '');
}

export function AgentInstructions({
    agent,
    onUpdate,
    canEdit,
}: {
    agent: Agent;
    onUpdate: (data: Partial<Agent>) => void;
    canEdit: boolean;
}) {
    const [instruction, setInstruction] = useState(() => normalize(agent.instruction || ''));

    // Re-sync when the agent itself changes underneath (a save landing, a
    // different agent routed into the same page).
    useEffect(() => {
        const incoming = normalize(agent.instruction || '');
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setInstruction((current) => (normalize(current) === incoming ? current : incoming));
    }, [agent.id, agent.instruction]);

    useEffect(() => {
        const next = normalize(instruction);
        if (next === normalize(agent.instruction || '')) return;

        const timer = setTimeout(() => onUpdate({ instruction: next }), 450);
        return () => clearTimeout(timer);
    }, [agent.instruction, instruction, onUpdate]);

    return (
        <section className="resource-card agent-instructions" data-edu="agent-instructions">
            <p className="resource-card-eyebrow">Instructions</p>
            <MarkdownEditor
                content={instruction}
                onChange={setInstruction}
                editable={canEdit}
                showSelectionToolbar
                readableProse
                placeholder="Tell the agent how to behave, what to do, and which rules to follow…"
                className="min-h-0"
                editorClassName="agent-instructions-prose bg-transparent shadow-none"
            />
        </section>
    );
}
