'use client';

import { WorkspaceFilesPane } from '@/components/workspace/workspace-files-pane';

/**
 * The agent's computer, in one panel.
 *
 * Files only for now. The live browser view that shared this panel was built on
 * a control channel only one sandbox provider could answer, so it was taken out
 * rather than left to fail everywhere else; it returns here as a second tab once
 * the relay lands. Kept as its own component so that is a change to this file
 * and not to the conversation layout.
 */
export function ComputerPanel({ conversationId }: { conversationId?: string }) {
    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
                <WorkspaceFilesPane conversationId={conversationId} />
            </div>
        </div>
    );
}
