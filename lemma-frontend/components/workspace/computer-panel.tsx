'use client';

import { useState } from 'react';

import { BrowserPane } from '@/components/workspace/browser-pane';
import { Button } from '@/components/ui/button';
import { WorkspaceFilesPane } from '@/components/workspace/workspace-files-pane';
import { cn } from '@/lib/utils';

type Tab = 'files' | 'browser';

/**
 * The agent's computer, in one panel.
 *
 * Two tabs, and the browser one is deliberately not the default: attaching
 * starts a browser if none is running, and a panel that did that on render
 * would hold a few hundred megabytes open for as long as it was on screen.
 */
export function ComputerPanel({
    workspaceCwd,
    conversationId,
}: {
    workspaceCwd?: string;
    conversationId?: string;
}) {
    const [tab, setTab] = useState<Tab>('files');

    return (
        <div className="flex h-full min-h-0 flex-col gap-2">
            <div className="flex items-center gap-1 px-1">
                {(['files', 'browser'] as const).map((name) => (
                    <Button
                        key={name}
                        variant="quiet"
                        size="xs"
                        onClick={() => setTab(name)}
                        aria-pressed={tab === name}
                        className={cn(
                            tab === name
                                ? 'text-[var(--text-primary)]'
                                : 'text-[var(--text-tertiary)]',
                        )}
                    >
                        {name === 'files' ? 'Files' : 'Browser'}
                    </Button>
                ))}
            </div>

            <div className="min-h-0 flex-1">
                {tab === 'files' ? (
                    <WorkspaceFilesPane workspaceCwd={workspaceCwd} />
                ) : (
                    // VNC shows this person's whole sandbox display, shared by
                    // every conversation's agent -- but *whether a browser is
                    // even running* is checked per session, and this
                    // conversation's own agent commands run in a session named
                    // for it (`run_browser_script`), not the shared default.
                    // Without this the pane checked the wrong session and
                    // refused forever with "no browser running" while the
                    // agent's browser was live the whole time.
                    <BrowserPane conversationId={conversationId} />
                )}
            </div>
        </div>
    );
}
