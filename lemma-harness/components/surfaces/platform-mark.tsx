'use client';

import Image from 'next/image';

import { getSurfaceDefinition } from '@/lib/surfaces/registry';
import { cn } from '@/lib/utils';

/**
 * The mark that stands for a surface platform: a brand logo, or a glyph for the
 * platform that isn't a brand.
 *
 * The fallback used to be a `MessageCircle` that CSS then hid outright, so the
 * one platform with no logo — email, the address every agent gets — drew an
 * empty bordered box wherever it appeared. A platform with neither logo nor
 * glyph is a registry gap, and a speech bubble is the wrong shape for whatever
 * it turns out to be, so the mark renders nothing rather than mislabelling it.
 */
export function PlatformMark({
    platform,
    size = 'md',
    className,
}: {
    platform: string;
    /** `sm` is for inline runs — a card footer, a dense list row. */
    size?: 'sm' | 'md';
    className?: string;
}) {
    const definition = getSurfaceDefinition(platform);
    const Glyph = definition?.glyph;

    if (!definition?.logoSrc && !Glyph) return null;

    const px = size === 'sm' ? 14 : 16;

    return (
        <span
            className={cn('surface-platform-mark shrink-0', size === 'sm' && 'surface-platform-mark-sm', className)}
            data-platform={platform.toLowerCase()}
            data-mark={definition.logoSrc ? 'logo' : 'glyph'}
        >
            {definition.logoSrc ? (
                <Image
                    src={definition.logoSrc}
                    alt=""
                    width={px}
                    height={px}
                    className="surface-platform-logo"
                    aria-hidden="true"
                />
            ) : Glyph ? (
                <Glyph className="surface-platform-glyph" aria-hidden />
            ) : null}
        </span>
    );
}
