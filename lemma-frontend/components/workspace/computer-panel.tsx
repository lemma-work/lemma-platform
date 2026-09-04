'use client';

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { AppWindow, Folder } from '@/components/ui/icons';
import { WorkspaceFilesPane } from '@/components/workspace/workspace-files-pane';
import { WorkspaceScreenPane } from '@/components/workspace/workspace-screen-pane';
import { cn } from '@/lib/utils';

type Tab = 'files' | 'screen';

/**
 * The agent's computer, in one panel.
 *
 * Files and screen are two views of one machine — "what is this thing doing" —
 * so splitting them across two surfaces made you choose before you knew which
 * half you wanted. They are tabs rather than routes because neither means
 * anything outside the conversation it belongs to.
 */
export function ComputerPanel({ conversationId }: { conversationId?: string }) {
    const [tab, setTab] = useState<Tab>('files');

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center gap-1 border-b border-[var(--row-border)] px-2 py-1.5">
                <Button
                    variant="quiet"
                    size="xs"
                    onClick={() => setTab('files')}
                    className={cn(tab === 'files' && 'text-[var(--text-primary)]')}
                >
                    <Folder className="mr-1.5 size-3.5" />
                    Files
                </Button>
                <Button
                    variant="quiet"
                    size="xs"
                    onClick={() => setTab('screen')}
                    className={cn(tab === 'screen' && 'text-[var(--text-primary)]')}
                >
                    <AppWindow className="mr-1.5 size-3.5" />
                    Screen
                </Button>
            </div>
            <div className="min-h-0 flex-1">
                {tab === 'files' ? (
                    <WorkspaceFilesPane conversationId={conversationId} />
                ) : (
                    <WorkspaceScreenPane />
                )}
            </div>
        </div>
    );
}
