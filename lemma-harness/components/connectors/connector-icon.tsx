'use client';

import { useState } from 'react';

import { cn } from '@/lib/utils';

/**
 * Brand marks for connectors the catalog can't supply an icon for.
 *
 * Composio toolkits carry a `logo` we store on import, but the apps Lemma
 * supports natively are synced from `lemma_apps_config.json` and never pass
 * through that path — so without this map our own first-party connectors are
 * the only ones in the list rendering without a brand mark.
 */
const LOCAL_CONNECTOR_LOGOS: Record<string, string> = {
    slack: '/connector-logos/slack.svg',
    microsoft_teams: '/connector-logos/teams.svg',
    telegram: '/connector-logos/telegram.svg',
    whatsapp: '/connector-logos/whatsapp.svg',
    gmail: '/connector-logos/gmail.svg',
    outlook: '/connector-logos/outlook.svg',
    hubspot: '/connector-logos/hubspot.svg',
};

/** Tint classes are defined in styles/features/connectors.css. */
const MONOGRAM_TINT_COUNT = 8;

const tintClassFor = (seed: string): string => {
    let hash = 0;
    for (let index = 0; index < seed.length; index += 1) {
        hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
    }
    return `connector-monogram-${(hash % MONOGRAM_TINT_COUNT) + 1}`;
};

/** First letter of each of the first two words — "Google Sheets" → "GS". */
const monogramFor = (label: string): string => {
    const words = label.trim().split(/\s+/).slice(0, 2);
    const initials = words.map((word) => word.charAt(0)).join('');
    return (initials || '?').toUpperCase();
};

export const resolveConnectorLogo = (
    connectorId: string,
    icon?: string | null,
): string | null => icon || LOCAL_CONNECTOR_LOGOS[connectorId] || null;

const SIZES = {
    sm: { box: 'h-8 w-8 rounded-lg', text: 'connector-monogram-sm' },
    md: { box: 'h-10 w-10 rounded-lg', text: 'text-sm' },
    lg: { box: 'h-12 w-12 rounded-xl', text: 'text-base' },
} as const;

export function ConnectorIcon({
    connectorId,
    icon,
    label,
    size = 'md',
    className,
}: {
    connectorId: string;
    icon?: string | null;
    label: string;
    size?: keyof typeof SIZES;
    className?: string;
}) {
    const [failed, setFailed] = useState(false);
    const src = resolveConnectorLogo(connectorId, icon);
    const dimensions = SIZES[size];

    if (src && !failed) {
        return (
            <div
                className={cn(
                    'connector-logo-tile flex shrink-0 items-center justify-center overflow-hidden p-1.5',
                    dimensions.box,
                    className,
                )}
            >
                {/* Plain <img>: connector logos come from arbitrary catalog CDNs, and an
                    unlisted host would make next/image throw rather than degrade. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                    src={src}
                    alt=""
                    aria-hidden="true"
                    loading="lazy"
                    className="h-full w-full object-contain"
                    onError={() => setFailed(true)}
                />
            </div>
        );
    }

    return (
        <div
            aria-hidden="true"
            className={cn(
                'connector-monogram flex shrink-0 items-center justify-center font-medium',
                tintClassFor(connectorId),
                dimensions.box,
                dimensions.text,
                className,
            )}
        >
            {monogramFor(label)}
        </div>
    );
}
