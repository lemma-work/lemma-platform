/* eslint-disable no-restricted-syntax -- Next ImageResponse renders with Satori, so its 1200x630 layout requires serializable inline styles rather than app CSS. */
import { ImageResponse } from 'next/og';
import type { NextRequest } from 'next/server';

import {
    resolveSocialCardSpec,
    SOCIAL_CARD_COLORS,
    SOCIAL_CARD_HEIGHT,
    SOCIAL_CARD_LAYERS,
    SOCIAL_CARD_WIDTH,
    socialCardTitleSize,
} from '@/lib/share/social-card';

export const runtime = 'edge';

const colors = SOCIAL_CARD_COLORS;

/** Left column is the message; the right plate carries the pod motif. */
const PLATE_WIDTH = 452;
const TEXT_WIDTH = SOCIAL_CARD_WIDTH - PLATE_WIDTH;

/**
 * The Lemma mark — three rising bars. Drawn rather than fetched so the card
 * stays hermetic on the edge and never renders half a logo on a slow network.
 */
function Wordmark() {
    return (
        <div style={{ display: 'flex', alignItems: 'flex-end', height: 34 }}>
            <div style={{ width: 9, height: 13, borderRadius: 2, background: colors.ink }} />
            <div style={{ width: 9, height: 22, borderRadius: 2, marginLeft: 5, background: colors.ink }} />
            <div style={{ width: 9, height: 31, borderRadius: 2, marginLeft: 5, background: colors.ink }} />
            <div
                style={{
                    marginLeft: 15,
                    fontSize: 26,
                    fontWeight: 700,
                    letterSpacing: -0.9,
                    color: colors.ink,
                }}
            >
                Lemma
            </div>
        </div>
    );
}

/**
 * The pod, as the product describes it: data at the bottom, then functions,
 * then agents and workflows, then the apps people actually meet. Tilted into a
 * shallow stack so it reads as depth at timeline thumbnail size — where a
 * wireframe mock would dissolve into grey mush.
 */
function PodStack({ accent, top }: { accent: string; top: string }) {
    // The thing being shared rides on top; the rest of the pod stays in the
    // order the product builds it, so the card always shows what it sits on.
    const rest = SOCIAL_CARD_LAYERS.filter((layer) => layer.key !== top).reverse();
    const lead = SOCIAL_CARD_LAYERS.find((layer) => layer.key === top);
    const layers = lead ? [lead, ...rest] : rest;

    return (
        <div
            style={{
                display: 'flex',
                flexDirection: 'column',
                transform: 'skewY(-9deg)',
            }}
        >
            {layers.map((layer, index) => {
                const isTop = index === 0;
                return (
                    <div
                        key={layer.label}
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            width: 262,
                            height: 70,
                            marginTop: index === 0 ? 0 : 20,
                            marginLeft: index * 10,
                            paddingLeft: 20,
                            paddingRight: 20,
                            borderRadius: 16,
                            background: isTop ? accent : colors.card,
                            border: `2px solid ${isTop ? accent : colors.border}`,
                            boxShadow: '0 16px 28px -24px rgba(24, 24, 22, 0.45)',
                        }}
                    >
                        <div
                            style={{
                                width: 13,
                                height: 13,
                                borderRadius: 7,
                                background: isTop ? colors.card : layer.color,
                            }}
                        />
                        <div
                            style={{
                                marginLeft: 13,
                                fontSize: 20,
                                fontWeight: 600,
                                letterSpacing: -0.3,
                                color: isTop ? colors.card : colors.inkSoft,
                            }}
                        >
                            {layer.label}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

export function GET(request: NextRequest) {
    const card = resolveSocialCardSpec({
        variant: request.nextUrl.searchParams.get('variant'),
        title: request.nextUrl.searchParams.get('title'),
        detail: request.nextUrl.searchParams.get('detail'),
        label: request.nextUrl.searchParams.get('label'),
    });

    return new ImageResponse(
        (
            <div
                style={{
                    width: '100%',
                    height: '100%',
                    display: 'flex',
                    background: colors.paper,
                    color: colors.ink,
                    fontFamily: 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif',
                }}
            >
                {/* Message */}
                <div
                    style={{
                        width: TEXT_WIDTH,
                        height: '100%',
                        padding: '64px 56px 56px 72px',
                        display: 'flex',
                        flexDirection: 'column',
                    }}
                >
                    <Wordmark />

                    <div
                        style={{
                            marginTop: 76,
                            display: 'flex',
                            alignItems: 'center',
                        }}
                    >
                        <div style={{ width: 28, height: 3, borderRadius: 2, background: card.accent }} />
                        <div
                            style={{
                                marginLeft: 14,
                                fontSize: 17,
                                fontWeight: 700,
                                letterSpacing: 3.4,
                                color: card.accent,
                            }}
                        >
                            {card.eyebrow}
                        </div>
                    </div>

                    <div
                        style={{
                            marginTop: 26,
                            maxWidth: 620,
                            fontSize: socialCardTitleSize(card.title),
                            fontWeight: 700,
                            letterSpacing: -3,
                            lineHeight: 1.02,
                            color: colors.ink,
                        }}
                    >
                        {card.title}
                    </div>

                    <div
                        style={{
                            marginTop: 22,
                            maxWidth: 580,
                            fontSize: 26,
                            lineHeight: 1.3,
                            color: colors.muted,
                        }}
                    >
                        {card.detail}
                    </div>

                    <div
                        style={{
                            marginTop: 'auto',
                            paddingTop: 22,
                            borderTop: `2px solid ${colors.rule}`,
                            display: 'flex',
                            alignItems: 'center',
                        }}
                    >
                        <div
                            style={{
                                fontSize: 19,
                                fontWeight: 500,
                                letterSpacing: -0.2,
                                color: colors.inkSoft,
                            }}
                        >
                            {card.label}
                        </div>
                    </div>
                </div>

                {/* Pod plate */}
                <div
                    style={{
                        width: PLATE_WIDTH,
                        height: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: colors.panel,
                        borderLeft: `2px solid ${colors.border}`,
                    }}
                >
                    <PodStack accent={card.accent} top={card.layer} />
                </div>
            </div>
        ),
        {
            width: SOCIAL_CARD_WIDTH,
            height: SOCIAL_CARD_HEIGHT,
            headers: {
                'Cache-Control': 'public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800',
            },
        },
    );
}
