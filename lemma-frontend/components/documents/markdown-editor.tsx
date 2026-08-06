'use client';

import { useEditor, EditorContent, type Editor } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import { Markdown } from 'tiptap-markdown';

type MarkdownEnabledEditor = Editor & {
    storage: {
        markdown: {
            getMarkdown: () => string;
        };
    };
};

interface MarkdownEditorProps {
    content: string;
    onChange: (content: string) => void;
    editable?: boolean;
    className?: string;
    editorClassName?: string;
    placeholder?: string;
    onSubmitShortcut?: () => void;
    readableProse?: boolean;
    /**
     * Show formatting controls over a selection. Off by default: markdown
     * shortcuts are enough where the writer already knows them, and a floating
     * toolbar in a dense pane is noise.
     */
    showSelectionToolbar?: boolean;
}

const SELECTION_MARKS = [
    { name: 'bold', label: 'B', title: 'Bold', className: 'font-semibold' },
    { name: 'italic', label: 'I', title: 'Italic', className: 'italic' },
    { name: 'code', label: '<>', title: 'Code', className: 'font-mono text-xs' },
] as const;

const SELECTION_BLOCKS = [
    { level: 1 as const, label: 'H1', title: 'Heading 1' },
    { level: 2 as const, label: 'H2', title: 'Heading 2' },
];

export function MarkdownEditor({
    content,
    onChange,
    editable = true,
    className,
    editorClassName,
    placeholder = 'Start writing...',
    onSubmitShortcut,
    readableProse = false,
    showSelectionToolbar = false,
}: MarkdownEditorProps) {
    const lastEmittedMarkdownRef = useRef(content);

    const getMarkdown = (editor: Editor) => {
        return (editor as MarkdownEnabledEditor).storage.markdown.getMarkdown();
    };

    const editor = useEditor({
        extensions: [
            StarterKit,
            // Marker classes only. How a table looks belongs to the document
            // stylesheet, which sets it as data rather than as a boxed form;
            // utilities pinned here would fight those rules cell by cell.
            Table.configure({
                resizable: true,
                HTMLAttributes: { class: 'lemma-markdown-table' },
            }),
            TableRow,
            TableHeader,
            TableCell,
            Placeholder.configure({
                placeholder,
            }),
            Markdown.configure({
                html: false,
                transformPastedText: true,
                transformCopiedText: true,
            })
        ],
        content,
        editable,
        editorProps: {
            attributes: {
                class: cn(
                    'tiptap-editor prose prose-neutral dark:prose-invert min-h-[200px] text-[var(--text-primary)] focus:outline-none',
                    readableProse ? 'lemma-markdown-editor' : 'max-w-none',
                    editorClassName
                ),
            },
            handleKeyDown: (_view, event) => {
                if (onSubmitShortcut && (event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                    event.preventDefault();
                    onSubmitShortcut();
                    return true;
                }

                return false;
            },
        },
        onUpdate: ({ editor, transaction }) => {
            if (!transaction.docChanged || !editor.isFocused) {
                return;
            }
            const markdown = getMarkdown(editor);
            lastEmittedMarkdownRef.current = markdown;
            onChange(markdown);
        },
        immediatelyRender: false,
    });

    useEffect(() => {
        if (!editor) return;

        const currentMarkdown = getMarkdown(editor);
        if (content === currentMarkdown || content === lastEmittedMarkdownRef.current) {
            return;
        }

        editor.commands.setContent(content);
        lastEmittedMarkdownRef.current = content;
    }, [content, editor]);

    useEffect(() => {
        if (editor) {
            editor.setEditable(editable);
        }
    }, [editable, editor]);

    if (!editor) {
        return null;
    }

    return (
        <div className={cn("relative min-h-[200px]", className)}>
            {showSelectionToolbar && editable ? (
                <BubbleMenu editor={editor} className="markdown-selection-toolbar">
                    {SELECTION_MARKS.map((mark) => (
                        <button
                            key={mark.name}
                            type="button"
                            className={cn('markdown-selection-item', mark.className)}
                            data-active={editor.isActive(mark.name)}
                            title={mark.title}
                            aria-label={mark.title}
                            onClick={() => editor.chain().focus().toggleMark(mark.name).run()}
                        >
                            {mark.label}
                        </button>
                    ))}
                    <span className="markdown-selection-divider" aria-hidden />
                    {SELECTION_BLOCKS.map((block) => (
                        <button
                            key={block.level}
                            type="button"
                            className="markdown-selection-item"
                            data-active={editor.isActive('heading', { level: block.level })}
                            title={block.title}
                            aria-label={block.title}
                            onClick={() => editor.chain().focus().toggleHeading({ level: block.level }).run()}
                        >
                            {block.label}
                        </button>
                    ))}
                    <button
                        type="button"
                        className="markdown-selection-item"
                        data-active={editor.isActive('bulletList')}
                        title="Bulleted list"
                        aria-label="Bulleted list"
                        onClick={() => editor.chain().focus().toggleBulletList().run()}
                    >
                        •
                    </button>
                </BubbleMenu>
            ) : null}
            <EditorContent editor={editor} />
        </div>
    );
}
