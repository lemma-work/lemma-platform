'use client';

/**
 * The whole sandbox, at full size: a tree on the left and a file on the right.
 *
 * The compact pane beside a conversation stays what it is -- a peek at the
 * directory a run is working in. This is the other thing people wanted: a
 * place to look through the machine, with folders that open and stay open and
 * a file you can actually read.
 *
 * Both render the same file. `file-preview.tsx` owns "what can this be shown
 * as", because two answers to that question is how one of them comes to print
 * an MP4 as text.
 */

import {
    useCallback,
    useEffect,
    useRef,
    useState,
    useSyncExternalStore,
} from 'react';
import { Tree } from 'react-arborist';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { useTheme } from 'next-themes';
import Editor from '@monaco-editor/react';

import { FileTypeIcon } from '@/components/documents/file-type-icon';
import { Button } from '@/components/ui/button';
import { ChevronDown, ChevronRight, Folder } from '@/components/ui/icons';
import { useDirectoryTree, type TreeNode } from '@/lib/hooks/use-directory-tree';
import { useWorkspaceFile, WORKSPACE_ROOT } from '@/lib/hooks/use-workspace-files';
import {
    DownloadLink,
    FileFacts,
    MediaPreview,
    formatSize,
    previewKindOf,
} from './file-preview';
import { cn } from '@/lib/utils';

/** Monaco's name for the language, from the only clue a path gives. */
const LANGUAGES: Record<string, string> = {
    css: 'css',
    go: 'go',
    html: 'html',
    java: 'java',
    js: 'javascript',
    json: 'json',
    jsx: 'javascript',
    md: 'markdown',
    py: 'python',
    rb: 'ruby',
    rs: 'rust',
    sh: 'shell',
    sql: 'sql',
    toml: 'ini',
    ts: 'typescript',
    tsx: 'typescript',
    yaml: 'yaml',
    yml: 'yaml',
};

function languageOf(path: string): string {
    const name = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
    const dot = name.lastIndexOf('.');
    return LANGUAGES[dot >= 0 ? name.slice(dot + 1) : ''] ?? 'plaintext';
}

/**
 * A file's text, in the editor this app already ships.
 *
 * Read-only. Monaco is here for what it gives a *reader* -- syntax colour,
 * line numbers, folding, find -- and the compact pane keeps its plain `<pre>`
 * because none of that is worth an editor's weight in a sidebar.
 */
function TextPreview({ path, text }: { path: string; text: string }) {
    const { resolvedTheme } = useTheme();
    // Monaco reads the theme once at mount and `next-themes` answers
    // `undefined` until it has, so rendering before then picks light and
    // keeps it. `useSyncExternalStore` is how `function-editor.tsx` asks the
    // same question -- a hydration-safe "are we on the client yet" that does
    // not set state from an effect.
    const mounted = useSyncExternalStore(
        () => () => {},
        () => true,
        () => false,
    );
    if (!mounted) return null;

    return (
        <Editor
            height="100%"
            language={languageOf(path)}
            value={text}
            theme={resolvedTheme === 'dark' ? 'vs-dark' : 'vs-light'}
            options={{
                readOnly: true,
                domReadOnly: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                fontSize: 13,
                renderWhitespace: 'none',
                // A viewer scrolls; it does not need a cursor blinking at it.
                renderLineHighlight: 'none',
            }}
        />
    );
}

function Preview({ path, sizeBytes }: { path: string; sizeBytes: number }) {
    const kind = previewKindOf(path);
    const { data, isPending, error } = useWorkspaceFile(path, kind !== 'text', sizeBytes);

    if (isPending) {
        return <Centered>Reading…</Centered>;
    }
    if (error) {
        return (
            <Centered>
                This file could not be read. It may have been removed since the list was
                taken.
            </Centered>
        );
    }
    if (!data) return null;

    if (data.tooLarge) {
        return (
            <div className="flex flex-col items-start gap-3 p-4">
                <FileFacts path={path} sizeBytes={data.sizeBytes} />
                <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                    {formatSize(data.sizeBytes)} is more text than this will show.
                    Download it, or ask the agent to summarise it.
                </p>
                <DownloadLink blob={data.blob} path={path} />
            </div>
        );
    }

    if (kind === 'text' && data.text !== null) {
        return (
            <div className="flex h-full min-h-0 flex-col">
                <div className="min-h-0 flex-1">
                    <TextPreview path={path} text={data.text} />
                </div>
                <div className="border-t border-[var(--row-border)] px-3 py-2">
                    <DownloadLink blob={data.blob} path={path} />
                </div>
            </div>
        );
    }

    return (
        <div className="flex flex-col items-start gap-3 overflow-auto p-4">
            <MediaPreview
                key={path}
                kind={kind}
                blob={data.blob}
                path={path}
                sizeBytes={data.sizeBytes}
            />
            <DownloadLink blob={data.blob} path={path} />
        </div>
    );
}

function Centered({ children }: { children: React.ReactNode }) {
    return (
        <div className="flex h-full items-center justify-center p-8 text-center text-sm text-[var(--text-tertiary)]">
            {children}
        </div>
    );
}

/**
 * The tree is virtualized, which means it wants a pixel height rather than a
 * flexbox one. Measured instead of guessed: a hardcoded height is how a tree
 * comes to scroll inside a container that is not scrolling.
 */
function useMeasured() {
    const ref = useRef<HTMLDivElement>(null);
    const [size, setSize] = useState({ width: 0, height: 0 });
    useEffect(() => {
        const node = ref.current;
        if (!node) return;
        const observer = new ResizeObserver((entries) => {
            const box = entries[0]?.contentRect;
            if (box) setSize({ width: box.width, height: box.height });
        });
        observer.observe(node);
        return () => observer.disconnect();
    }, []);
    return [ref, size] as const;
}

export function FileExplorer({
    root = WORKSPACE_ROOT,
    selected,
    onSelect,
}: {
    root?: string;
    /** The open file, held by the page so it can live in the URL. */
    selected: string | null;
    onSelect: (path: string, sizeBytes: number) => void;
}) {
    const { data, onToggle, sleeping, loading } = useDirectoryTree(root);
    const [treeRef, treeSize] = useMeasured();
    const [selectedSize, setSelectedSize] = useState(0);

    const activate = useCallback(
        (node: TreeNode) => {
            if (node.kind === 'directory') return;
            setSelectedSize(node.sizeBytes);
            onSelect(node.id, node.sizeBytes);
        },
        [onSelect],
    );

    if (sleeping) {
        return (
            <Centered>
                This computer is asleep. Its files are still there — open the browser or
                send the agent a message to wake it.
            </Centered>
        );
    }

    return (
        <Group orientation="horizontal" className="h-full min-h-0">
            <Panel defaultSize="28" minSize="15" className="min-h-0">
                <div ref={treeRef} className="h-full min-h-0 overflow-hidden">
                    {treeSize.height > 0 ? (
                        <Tree<TreeNode>
                            data={data}
                            openByDefault={false}
                            width={treeSize.width}
                            height={treeSize.height}
                            indent={14}
                            rowHeight={28}
                            onToggle={onToggle}
                            onActivate={(node) => activate(node.data)}
                            disableDrag
                            disableDrop
                            disableEdit
                        >
                            {Row}
                        </Tree>
                    ) : null}
                    {loading && !data.length ? (
                        <p className="px-3 py-2 text-sm text-[var(--text-tertiary)]">
                            Reading…
                        </p>
                    ) : null}
                </div>
            </Panel>
            <Separator className="w-px bg-[var(--row-border)]" />
            <Panel minSize="30" className="min-h-0">
                {selected ? (
                    <Preview path={selected} sizeBytes={selectedSize} />
                ) : (
                    <Centered>Pick a file to read it.</Centered>
                )}
            </Panel>
        </Group>
    );
}

/** One row. `react-arborist` hands us the node and expects us to draw it. */
function Row({
    node,
    style,
    dragHandle,
}: {
    node: {
        data: TreeNode;
        isOpen: boolean;
        isSelected: boolean;
        isLeaf: boolean;
        toggle: () => void;
        select: () => void;
        activate: () => void;
    };
    style: React.CSSProperties;
    dragHandle?: (element: HTMLDivElement | null) => void;
}) {
    const directory = node.data.kind === 'directory';
    return (
        <div
            ref={dragHandle}
            // The virtualizer's own absolute positioning for this row:
            // runtime geometry by definition, since the list computes a top
            // offset per row, and there is no class that can carry it.
            // eslint-disable-next-line no-restricted-syntax
            style={style}
            // The selected fill sits on the row, not on the button inside it:
            // a background on a `Button` is a skin override, and the row is
            // the thing that is selected anyway.
            className={cn('pr-1', node.isSelected && 'bg-[var(--surface-2)]')}
        >
            <Button
                variant="quiet"
                size="sm"
                className="h-7 w-full justify-start gap-1.5 rounded-sm px-1 font-normal"
                onClick={() => {
                    if (directory) {
                        node.toggle();
                        return;
                    }
                    node.select();
                    node.activate();
                }}
            >
                {directory ? (
                    node.isOpen ? (
                        <ChevronDown className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                    ) : (
                        <ChevronRight className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                    )
                ) : (
                    // An indent where the arrow would be, so names line up
                    // whatever the row is.
                    <span className="size-3.5 shrink-0" />
                )}
                {directory ? (
                    <Folder className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                ) : (
                    <FileTypeIcon filename={node.data.name} size="sm" />
                )}
                <span className="truncate text-left">{node.data.name}</span>
            </Button>
        </div>
    );
}
