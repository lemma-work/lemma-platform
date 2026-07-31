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
import type { Function as FunctionType } from '@/lib/types';

/**
 * What the function takes in and hands back.
 *
 * Behind a dialog rather than spread across the page: the contract matters to
 * whoever calls it, but editing it is an occasional job, and a schema builder
 * left open inline is most of a screen spent on something rarely touched. The
 * page states the contract in one line and opens this to change it.
 */
export function FunctionContractDialog({
    open,
    onOpenChange,
    functionData,
    onUpdate,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    functionData: FunctionType;
    onUpdate: (data: Partial<FunctionType>) => void;
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

    const field = (which: 'input' | 'output') => (which === 'input' ? 'input_schema' as const : 'output_schema' as const);
    const schemaFor = (which: 'input' | 'output') => (
        which === 'input' ? functionData.input_schema || {} : functionData.output_schema || {}
    );

    const pane = (which: 'input' | 'output') => (
        mode === 'builder' ? (
            <SchemaBuilder
                value={schemaFor(which)}
                onChange={(schema) => onUpdate({ [field(which)]: schema })}
            />
        ) : (
            <div className="h-80 overflow-hidden rounded-lg bg-[var(--bg-canvas)] shadow-[var(--shadow-xs)]">
                <Editor
                    height="100%"
                    defaultLanguage="json"
                    theme={monacoTheme}
                    value={JSON.stringify(schemaFor(which), null, 2)}
                    onChange={(value) => {
                        try {
                            if (value) onUpdate({ [field(which)]: JSON.parse(value) });
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
                        The fields this function takes and the shape it returns.
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
