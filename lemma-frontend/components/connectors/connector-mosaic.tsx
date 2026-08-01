'use client';

import { useMemo } from 'react';

import type { Connector } from '@/lib/types';
import { FEATURED_CONNECTOR_IDS } from './connector-categories';
import { resolveConnectorLogo } from './connector-icon';

/** Enough tiles to fill the band at desktop widths and clip, never to run dry. */
const MAX_TILES = 72;

/** Below this the band looks like a half-loaded grid, so we drop it entirely. */
const MIN_TILES = 14;

/**
 * The catalog's own logos as a masthead band.
 *
 * Deliberately not an illustration: the honest picture of "what can this
 * connect to" is the set of apps actually in the catalog, and it can never
 * drift out of date or show a brand mark we don't really support.
 *
 * Only cells that have a logo are drawn — an empty cell in a lattice reads as a
 * skeleton loader, not as texture.
 */
export function ConnectorMosaic({ connectors }: { connectors: Connector[] }) {
    const tiles = useMemo(() => {
        const featuredRank = new Map<string, number>(
            FEATURED_CONNECTOR_IDS.map((id, index) => [id, index]),
        );

        return connectors
            .map((connector) => ({
                id: connector.id,
                logo: resolveConnectorLogo(connector.id, connector.icon),
                rank: featuredRank.get(connector.id) ?? Number.MAX_SAFE_INTEGER,
            }))
            .filter((tile): tile is { id: string; logo: string; rank: number } => Boolean(tile.logo))
            .sort((a, b) => a.rank - b.rank)
            .slice(0, MAX_TILES);
    }, [connectors]);

    if (tiles.length < MIN_TILES) return null;

    return (
        <div aria-hidden="true" className="connector-mosaic pointer-events-none select-none">
            <div className="connector-mosaic-rail">
                {tiles.map((tile) => (
                    <div key={tile.id} className="connector-logo-tile connector-mosaic-tile">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={tile.logo} alt="" loading="lazy" className="h-full w-full object-contain" />
                    </div>
                ))}
            </div>
        </div>
    );
}
