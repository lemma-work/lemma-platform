'use client';

import { useState } from 'react';
import { Check, Copy, Download, Github, Link2, Share2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useExportPod } from '@/lib/hooks/use-pod-imports';

/** The pod's outbound surface: copy a shareable import link, download the
 * bundle, or (soon) publish to GitHub. This is beat 1 of the share loop. */
export function SharePodSheet({ podId, podName }: { podId: string; podName?: string }) {
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);
    const exportPod = useExportPod();

    const shareLink =
        typeof window !== 'undefined' ? `${window.location.origin}/import/p/${podId}` : '';

    const copyLink = async () => {
        try {
            await navigator.clipboard.writeText(shareLink);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Couldn’t copy the link');
        }
    };

    const onDownload = () => {
        exportPod.mutate(
            { podId, withData: true },
            {
                onSuccess: (filename) => toast.success(`Downloaded ${filename}`),
                onError: (e) => toast.error(e instanceof Error ? e.message : 'Export failed'),
            },
        );
    };

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button variant="secondary">
                    <Share2 className="mr-1.5 h-4 w-4" /> Share
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 p-3">
                <p className="text-sm font-medium text-[var(--text-primary)]">
                    Share {podName ?? 'this pod'}
                </p>
                <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">
                    Anyone in your org can import it as their own pod.
                </p>
                <div className="mt-3 flex items-center gap-2">
                    <div className="flex flex-1 items-center gap-1.5 overflow-hidden rounded-lg border border-[var(--border-subtle)] px-2.5 py-1.5 text-xs text-[var(--text-secondary)]">
                        <Link2 className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{shareLink.replace(/^https?:\/\//, '')}</span>
                    </div>
                    <Button variant="secondary" onClick={copyLink} aria-label="Copy link">
                        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    </Button>
                </div>
                <Button
                    variant="secondary"
                    className="mt-2 w-full justify-start"
                    loading={exportPod.isPending}
                    onClick={onDownload}
                >
                    <Download className="mr-2 h-4 w-4" /> Download bundle (.zip)
                </Button>
                <Button variant="secondary" className="mt-2 w-full justify-start" disabled>
                    <Github className="mr-2 h-4 w-4" /> Publish to GitHub — soon
                </Button>
            </PopoverContent>
        </Popover>
    );
}
