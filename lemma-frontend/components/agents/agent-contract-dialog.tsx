'use client';

import { useState, useSyncExternalStore } from 'react';
import { useTheme } from 'next-themes';
import Editor from '@monaco-editor/react';

import { SchemaBuilder } from '@/components/agents/schema-builder';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Code, Table as TableIcon } from '@/components/ui/icons';
import type { Agent } from '@/lib/types';

/**
 * The agent's contract: what it takes in, what it hands back.
 *
 * Structured only when the agent needs it — most agents talk in prose and
 * should leave both sides empty.
 */
export function AgentContractDialog({
    open,
    onOpenChange,
    agent,
    onUpdate,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    agent: Agent;
    onUpdate: (data: Partial<Agent>) => void;
}) {
    const [mode, setMode] = useState<'builder' | 'json'>('builder');
    const [tab, setTab] = useState<'input' | 'output'>('input');
    const { resolvedTheme } = useTheme();
    const mounted = useSyncExternalStore(
        () => () => { },
        () => true,
        () => false,
    );
    const monacoTheme = mounted && resolvedTheme === 'dark' ? 'vs-dark' : 'vs-light';

    const handleSchemaChange = (type: 'input_schema' | 'output_schema', schema: Record<string, unknown>) => {
        onUpdate({ [type]: schema });
    };

    const schemaFor = (which: 'input' | 'output') => (
        which === 'input' ? agent.input_schema || {} : agent.output_schema || {}
    );
    const fieldFor = (which: 'input' | 'output') => (
        which === 'input' ? 'input_schema' as const : 'output_schema' as const
    );

    const pane = (which: 'input' | 'output') => (
        mode === 'builder' ? (
            <SchemaBuilder
                value={schemaFor(which)}
                onChange={(schema) => handleSchemaChange(fieldFor(which), schema)}
            />
        ) : (
            <div className="h-96 overflow-hidden rounded-lg bg-[var(--bg-canvas)] shadow-[var(--shadow-xs)]">
                <Editor
                    height="100%"
                    defaultLanguage="json"
                    theme={monacoTheme}
                    value={JSON.stringify(schemaFor(which), null, 2)}
                    onChange={(value) => {
                        try {
                            if (value) handleSchemaChange(fieldFor(which), JSON.parse(value));
                        } catch {
                            // Ignore parse errors while typing
                        }
                    }}
                    options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: 'on' }}
                />
            </div>
        )
    );

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[86vh] max-w-4xl gap-0 overflow-hidden p-0">
                <DialogHeader className="border-b border-[color:var(--border-subtle)] px-5 py-4 pr-12 text-left">
                    <DialogTitle>Contract</DialogTitle>
                    <DialogDescription className="text-xs">
                        Structured fields, only where the agent needs them. Leave both empty and it works in prose.
                    </DialogDescription>
                </DialogHeader>

                <div className="max-h-[calc(86vh-10rem)] overflow-y-auto px-5 py-4">
                    <Tabs value={tab} onValueChange={(value) => setTab(value as 'input' | 'output')}>
                        <div className="mb-3 flex items-center justify-between gap-3">
                            <TabsList>
                                <TabsTrigger value="input">Takes</TabsTrigger>
                                <TabsTrigger value="output">Returns</TabsTrigger>
                            </TabsList>
                            <div className="segmented-control">
                                <button
                                    type="button"
                                    onClick={() => setMode('builder')}
                                    className="segmented-control-item min-w-0 px-2"
                                    data-active={mode === 'builder'}
                                    title="Visual builder"
                                    aria-label="Visual builder"
                                >
                                    <TableIcon className="h-4 w-4" />
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setMode('json')}
                                    className="segmented-control-item min-w-0 px-2"
                                    data-active={mode === 'json'}
                                    title="JSON editor"
                                    aria-label="JSON editor"
                                >
                                    <Code className="h-4 w-4" />
                                </button>
                            </div>
                        </div>

                        <TabsContent value="input" className="mt-0">{pane('input')}</TabsContent>
                        <TabsContent value="output" className="mt-0">{pane('output')}</TabsContent>
                    </Tabs>
                </div>

                <DialogFooter className="flex-row items-center border-t border-[color:var(--border-subtle)] px-5 py-3">
                    <Button type="button" size="sm" className="ml-auto" onClick={() => onOpenChange(false)}>
                        Done
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
