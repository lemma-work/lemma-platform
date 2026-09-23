'use client';

import { ReactNode, useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import { identityVariantSeed, parseResourceIcon } from '@/lib/utils/resource-icon-value';
import { ResourceIdentity } from '@/components/shared/resource-identity';
import type { LemmaIcon } from '@/components/ui/icons';
import type { IdentityState } from '@/lib/identity/seeded-identity';

interface ResourceIconProps {
    iconUrl?: string | null;
    alt: string;
    label?: string;
    fallback?: ReactNode;
    className?: string;
    imageClassName?: string;
    /**
     * Turns on the generated identity for resources that have no picture and no
     * emoji — which, before this, meant a grey glyph or two initials shared by
     * everything of the same type. Callers pass a stable seed (an id where one
     * exists) and the size they have styled the box to.
     */
    identitySeed?: string;
    identityKind?: 'being' | 'mark' | 'team';
    identityState?: IdentityState;
    identityGlyph?: LemmaIcon;
    identitySize?: number;
}

function getInitials(label?: string): string {
    const trimmed = label?.trim();
    if (!trimmed) return '?';
    const words = trimmed.split(/\s+/).slice(0, 2);
    return words.map((word) => word.charAt(0).toUpperCase()).join('');
}

export function ResourceIcon({
    iconUrl,
    alt,
    label,
    fallback,
    className,
    imageClassName,
    identitySeed,
    identityKind = 'being',
    identityState,
    identityGlyph,
    identitySize = 40,
}: ResourceIconProps) {
    const [imageFailed, setImageFailed] = useState(false);
    const initials = useMemo(() => getInitials(label), [label]);
    // A resource's icon field holds either a picture or a typed glyph; which
    // one is decided in exactly one place. Without this, an emoji reached the
    // `<img>` below, failed to load, and only then fell back — a broken-image
    // flash on every render of a pod someone gave an emoji to.
    const icon = useMemo(() => parseResourceIcon(iconUrl), [iconUrl]);
    const shouldShowImage = icon?.kind === 'url' && !imageFailed;
    const glyph = icon?.kind === 'glyph' ? icon.glyph : null;
    // The generated identity is a *last* resort, never an override: a picture
    // someone uploaded and an emoji someone typed are both explicit choices and
    // both outrank it. What it replaces is the nothing that used to be here.
    const shouldShowIdentity = !shouldShowImage && !glyph && Boolean(identitySeed);
    // A stored variant shifts which face is drawn, but it is still drawn from
    // this resource's own seed — so renaming nothing and picking nothing both
    // keep giving the same answer.
    const identityVariant = icon?.kind === 'identity' ? icon.variant : 0;

    return (
        <div
            className={cn(
                'relative flex items-center justify-center overflow-hidden rounded-lg border border-transparent text-[var(--text-secondary)]',
                shouldShowImage || shouldShowIdentity
                    ? 'bg-transparent'
                    : 'bg-[color:color-mix(in_srgb,var(--surface-2)_52%,transparent)]',
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
            ) : shouldShowIdentity && identitySeed ? (
                <ResourceIdentity
                    seed={identityVariantSeed(identitySeed, identityVariant)}
                    label={alt}
                    kind={identityKind}
                    state={identityState}
                    glyph={identityGlyph}
                    size={identitySize}
                />
            ) : fallback ? (
                fallback
            ) : (
                <span className="text-sm font-normal">{initials}</span>
            )}
        </div>
    );
}
