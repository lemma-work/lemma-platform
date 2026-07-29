'use client';

import Image from 'next/image';
import { useMemo, useState } from 'react';
import { Copy, Download } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
    buildPodShareCardCopy,
    podShareCardDataUrl,
    podShareCardFilename,
    renderPodShareCardPng,
} from '@/lib/share/pod-share-card';

interface PodShareCardProps {
    podName?: string | null;
    repoUrl: string;
}

export function PodShareCard({ podName, repoUrl }: PodShareCardProps) {
    const [copying, setCopying] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const input = useMemo(() => ({ podName, repoUrl }), [podName, repoUrl]);
    const previewUrl = useMemo(() => podShareCardDataUrl(input), [input]);

    async function handleCopyImage() {
        if (copying) return;
        setCopying(true);
        try {
            const blob = await renderPodShareCardPng(input);
            if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
                throw new Error('Image copy is unavailable in this browser.');
            }
            await navigator.clipboard.write([
                new ClipboardItem({ 'image/png': blob }),
            ]);
            toast.success('Share card copied');
        } catch {
            try {
                await navigator.clipboard.writeText(buildPodShareCardCopy(input));
                toast.success('Share copy copied', {
                    description: 'This browser could not copy the image, so Lemma copied the post instead.',
                });
            } catch {
                toast.error('Could not copy the share card');
            }
        } finally {
            setCopying(false);
        }
    }

    async function handleDownload() {
        if (downloading) return;
        setDownloading(true);
        try {
            const blob = await renderPodShareCardPng(input);
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = podShareCardFilename(podName);
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
            toast.success('Share card downloaded');
        } catch {
            toast.error('Could not download the share card');
        } finally {
            setDownloading(false);
        }
    }

    return (
        <section className="space-y-3">
            <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)]">
                <Image
                    src={previewUrl}
                    alt={`${podName || 'Pod'} share card: Run it on Lemma`}
                    width={1200}
                    height={630}
                    unoptimized
                    className="h-auto w-full"
                />
            </div>
            <div className="flex gap-2">
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="flex-1"
                    onClick={handleCopyImage}
                    disabled={copying || downloading}
                >
                    <Copy className="mr-2 h-3.5 w-3.5" />
                    {copying ? 'Copying…' : 'Copy image'}
                </Button>
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="flex-1"
                    onClick={handleDownload}
                    disabled={copying || downloading}
                >
                    <Download className="mr-2 h-3.5 w-3.5" />
                    {downloading ? 'Preparing…' : 'Download PNG'}
                </Button>
            </div>
        </section>
    );
}
