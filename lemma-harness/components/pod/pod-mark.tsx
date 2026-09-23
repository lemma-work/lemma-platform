'use client';

import { cn } from '@/lib/utils';

/**
 * The fallback mark for a pod with no uploaded icon: its initials on the brand
 * fill, the same mark the sidebar header wears. Small, but it is what makes a
 * row in the switcher a place you can go rather than a line of text.
 *
 * It lived inside the workspace sidebar until home's pod cards needed it too. A
 * pod is violet wherever it appears — a second, differently-coloured mark on
 * the list you pick pods from would mean the product identifies the same object
 * two ways.
 */
export function PodMark({
    name,
    size = 'sm',
}: {
    name?: string | null;
    size?: 'sm' | 'lg';
}) {
    const initials = (name || 'Pod')
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map((part) => part.charAt(0).toUpperCase())
        .join('') || 'P';

    return (
        <span className={cn('lemma-pod-badge', size === 'lg' && 'lemma-pod-badge-lg')}>
            {initials}
        </span>
    );
}
