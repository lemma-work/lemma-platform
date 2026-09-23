'use client';

import { useState } from 'react';
import { FileText } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAttachDocumentMarkdown, useDetachDocumentMarkdown } from '@/lib/hooks/use-datastores';
import { cn } from '@/lib/utils';
import { StepLoader } from '@/components/brand/loader';

/** Preview types that are extracted into markdown and so accept a BYO override. */
const MARKDOWN_ATTACHABLE_PREVIEW_TYPES = new Set(['pdf', 'office', 'html', 'unsupported']);

export function canAttachDocumentMarkdown(previewType: string): boolean {
    return MARKDOWN_ATTACHABLE_PREVIEW_TYPES.has(previewType);
}

/** Whether this document's agent-facing text is one someone uploaded. */
export function usesUserMarkdown(metadata?: Record<string, unknown> | null): boolean {
    return metadata?.markdown_source === 'user';
}

export function MarkdownAttachmentControl({
    podId,
    datastoreName,
    filePath,
    metadata,
    disabled,
    open: controlledOpen,
    onOpenChange,
    showTrigger = true,
}: {
    podId: string;
    datastoreName: string;
    filePath: string;
    metadata?: Record<string, unknown> | null;
    disabled?: boolean;
    /**
     * Drive the dialog from outside when the way in is somewhere this component
     * cannot render — a menu item, say, whose menu unmounts on select and would
     * take a dialog nested inside it along.
     */
    open?: boolean;
    onOpenChange?: (open: boolean) => void;
    showTrigger?: boolean;
}) {
    const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
    const open = controlledOpen ?? uncontrolledOpen;
    const setOpen = onOpenChange ?? setUncontrolledOpen;
    const [markdownFile, setMarkdownFile] = useState<File | null>(null);
    const [imageFiles, setImageFiles] = useState<File[]>([]);
    const { mutate: attach, isPending: isAttaching } = useAttachDocumentMarkdown();
    const { mutate: detach, isPending: isDetaching } = useDetachDocumentMarkdown();

    const isUserMarkdown = usesUserMarkdown(metadata);
    const assetNames = Array.isArray(metadata?.markdown_asset_names)
        ? (metadata.markdown_asset_names as string[])
        : [];
    const isBusy = isAttaching || isDetaching;

    const reset = () => {
        setMarkdownFile(null);
        setImageFiles([]);
    };

    const submit = () => {
        if (!markdownFile) return;
        attach(
            { podId, datastoreName, file_path: filePath, markdown: markdownFile, images: imageFiles },
            {
                onSuccess: () => {
                    toast.success('Your markdown is now the document’s agent-facing text');
                    setOpen(false);
                    reset();
                },
                onError: (error) => toast.error(`Couldn’t attach markdown: ${error.message}`),
            }
        );
    };

    const handleDetach = () => {
        detach(
            { podId, datastoreName, file_path: filePath },
            {
                onSuccess: () => {
                    toast.success('Reverted to extracted text');
                    setOpen(false);
                    reset();
                },
                onError: (error) => toast.error(`Couldn’t revert markdown: ${error.message}`),
            }
        );
    };

    return (
        <>
            {showTrigger ? (
                <Tooltip>
                    <TooltipTrigger asChild>
                        <Button
                            type="button"
                            variant="quiet"
                            size="icon"
                            className={cn('h-8 w-8 rounded', isUserMarkdown && 'text-[var(--action-primary)]')}
                            onClick={() => setOpen(true)}
                            disabled={disabled}
                            aria-label="Document markdown"
                        >
                            <FileText className="h-4 w-4" />
                        </Button>
                    </TooltipTrigger>
                    <TooltipContent>{isUserMarkdown ? 'Using your markdown' : 'Attach your markdown'}</TooltipContent>
                </Tooltip>
            ) : null}

            <Dialog
                open={open}
                onOpenChange={(next) => {
                    setOpen(next);
                    if (!next) reset();
                }}
            >
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Document markdown</DialogTitle>
                        <DialogDescription>
                            Replace the agent-facing text of this document with your own markdown. The original file is
                            left untouched; images you upload resolve against the markdown’s <code>![](name.png)</code>{' '}
                            references.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="grid gap-4 py-1">
                        <div
                            className={cn(
                                'rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] p-3 text-sm',
                                isUserMarkdown ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
                            )}
                        >
                            {isUserMarkdown ? (
                                <>
                                    <p className="font-medium">Currently using your uploaded markdown.</p>
                                    {assetNames.length ? (
                                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                            Images: {assetNames.join(', ')}
                                        </p>
                                    ) : null}
                                </>
                            ) : (
                                <p>This document currently uses automatically extracted text.</p>
                            )}
                        </div>

                        <div className="grid gap-1.5">
                            <label className="type-eyebrow-medium">Markdown file</label>
                            <input
                                type="file"
                                accept=".md,.markdown,text/markdown"
                                onChange={(event) => setMarkdownFile(event.target.files?.[0] ?? null)}
                                className="text-sm text-[var(--text-secondary)] file:mr-3 file:rounded-md file:border file:border-[color:var(--border-subtle)] file:bg-[var(--field-bg)] file:px-3 file:py-1.5 file:text-sm file:text-[var(--text-primary)]"
                            />
                        </div>

                        <div className="grid gap-1.5">
                            <label className="type-eyebrow-medium">Images (optional)</label>
                            <input
                                type="file"
                                accept="image/*"
                                multiple
                                onChange={(event) => setImageFiles(Array.from(event.target.files ?? []))}
                                className="text-sm text-[var(--text-secondary)] file:mr-3 file:rounded-md file:border file:border-[color:var(--border-subtle)] file:bg-[var(--field-bg)] file:px-3 file:py-1.5 file:text-sm file:text-[var(--text-primary)]"
                            />
                            {imageFiles.length ? (
                                <p className="text-xs text-[var(--text-tertiary)]">{imageFiles.length} image(s) selected</p>
                            ) : (
                                <p className="text-xs text-[var(--text-tertiary)]">Name each image to match its markdown reference.</p>
                            )}
                        </div>
                    </div>

                    <DialogFooter className="sm:justify-between">
                        {isUserMarkdown ? (
                            <Button
                                variant="quiet"
                                onClick={handleDetach}
                                disabled={isBusy}
                                className="text-[var(--state-error)]"
                            >
                                {isDetaching ? <StepLoader size="sm" className="mr-2" /> : null}
                                Revert to extracted
                            </Button>
                        ) : (
                            <span />
                        )}
                        <div className="flex items-center gap-2">
                            <Button variant="secondary" onClick={() => setOpen(false)} disabled={isBusy}>
                                Cancel
                            </Button>
                            <Button variant="secondary" onClick={submit} disabled={!markdownFile || isBusy}>
                                {isAttaching ? <StepLoader size="sm" className="mr-2" /> : null}
                                {isUserMarkdown ? 'Replace markdown' : 'Attach markdown'}
                            </Button>
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
