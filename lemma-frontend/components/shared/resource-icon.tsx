'use client';

import { ReactNode, useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import { parseResourceIcon } from '@/lib/utils/resource-icon-value';

interface ResourceIconProps {
    iconUrl?: string | null;
    alt: string;
    label?: string;
    fallback?: ReactNode;
    className?: string;
    imageClassName?: string;
}

function getInitials(label?: string): string {
    const trimmed = label?.trim();
    if (!trimmed) return '?';
    const words = trimmed.split(/\s+/).slice(0, 2);
    return words.map((word) => word.charAt(0).toUpperCase()).join('');
}

export function ResourceIcon({ iconUrl, alt, label, fallback, className, imageClassName }: ResourceIconProps) {
    const [imageFailed, setImageFailed] = useState(false);
    const initials = useMemo(() => getInitials(label), [label]);
    // A resource's icon field holds either a picture or a typed glyph; which
    // one is decided in exactly one place. Without this, an emoji reached the
    // `<img>` below, failed to load, and only then fell back — a broken-image
    // flash on every render of a pod someone gave an emoji to.
    const icon = useMemo(() => parseResourceIcon(iconUrl), [iconUrl]);
    const shouldShowImage = icon?.kind === 'url' && !imageFailed;
    const glyph = icon?.kind === 'glyph' ? icon.glyph : null;

    return (
        <div
            className={cn(
                'relative flex items-center justify-center overflow-hidden rounded-lg border border-transparent text-[var(--text-secondary)]',
                shouldShowImage ? 'bg-transparent' : 'bg-[color:color-mix(in_srgb,var(--surface-2)_52%,transparent)]',
                className
            )}
        >
            {shouldShowImage ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                    src={icon.url}
                    alt={alt}
                    className={cn('h-full w-full object-cover', imageClassName)}
                    onError={() => setImageFailed(true)}
                />
            ) : glyph ? (
                // Sized from the box rather than from a font scale, because the
                // same component is drawn at 24px in the pod switcher and 44px
                // on home, and an emoji that ignores its container is the one
                // thing more obviously wrong than no emoji at all.
                <span className="resource-icon-glyph-box">
                    <span className="resource-icon-glyph" role="img" aria-label={alt}>
                        {glyph}
                    </span>
                </span>
            ) : fallback ? (
                fallback
            ) : (
                <span className="text-sm font-normal">{initials}</span>
            )}
        </div>
    );
}
